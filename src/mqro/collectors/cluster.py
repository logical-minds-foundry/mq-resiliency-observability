"""Pacemaker/Corosync/DRBD cluster-state collector — a contract-preserving extraction of the
lab's ``clusterstate.py``.

Stdlib-only, so this file deploys verbatim onto the Pacemaker/DRBD nodes and is also imported by
the unit tests. Pure parse functions turn ``crm_mon`` / ``stonith_admin`` / ``iscsiadm`` /
``systemctl is-active`` / ``drbdsetup`` output into rows; :func:`render_cluster_state_prom` projects
rows into node_exporter textfile lines; :func:`run` shells each source out bounded + non-blocking
(timeout -> no fresh sample -> the cell reads STALE) and writes the textfile atomically.

Two deliberate reuses keep this collector consistent with its siblings:

- The **DRBD parser is imported** from :mod:`mqro.collectors.drbd` (#15), not re-extracted here, so
  the Pacemaker and RDQM collectors project byte-identical ``cluster_drbd_*`` series.
- The **render/probe/textfile helpers** come from :mod:`mqro.collectors.base`, so every collector
  formats and writes the same way.

Two lab hardcodes are dropped so this runs against any deployment:

- The lab scoped resource placement to a single ``group[@id='mq_group']``. Here **every** resource
  *group* is reported (top-level STONITH ``fence_*`` resources stay excluded — they live outside any
  group), so no resource-group name is baked in. A queue manager under Pacemaker is a cluster
  *resource* whose id (e.g. ``mq_qm``) already travels in the crm_mon output, so — unlike the Native
  HA collector — this collector needs no queue-manager name from config: resource identities are
  sourced from crm_mon, and QM-as-a-variable is a dashboard-side concern.
- The output path is the profile's ``textfile_dir``, not the lab's baked ``/var/lib/...`` path.

There is no role flag: a node that cannot answer a source (crm_mon on a storage-only node, drbdsetup
on a cluster-only node) simply gets ``None`` back and that source's series go STALE.
"""

from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

from mqro.collectors.base import (
    last_write_lines,
    probe,
    write_textfile,
)
from mqro.collectors.base import (
    metric_line as _m,
)
from mqro.collectors.drbd import parse_drbd

if TYPE_CHECKING:
    from pathlib import Path

    from mqro.config import Profile

# The node_exporter textfile this collector writes into the profile's textfile directory.
_TEXTFILE_NAME = "mqro_cluster_state.prom"

# The cluster daemons whose liveness the cockpit shows. `systemctl is-active` reports every unit's
# real state (active/inactive/failed) with a non-zero exit for a down unit — a valid "down" reading,
# not a probe failure — so the daemons probe runs with ignore_rc (a timeout, by contrast, yields no
# reading at all -> STALE). A unit absent on this node simply reads not-active (0).
_DAEMON_UNITS = ("corosync", "pacemaker", "drbd")

# source -> (argv, timeout seconds). Timeouts sit well under a typical 5s scrape tick.
_COMMANDS: dict[str, tuple[list[str], int]] = {
    "crm": (["crm_mon", "--one-shot", "--output-as=xml"], 3),
    "drbd": (["drbdsetup", "status", "--verbose", "--statistics"], 2),
    "stonith": (["stonith_admin", "--history", "*"], 2),
    "iscsi": (["iscsiadm", "-m", "session"], 2),
}


def parse_crm(xml_text: str) -> dict[str, Any]:
    """Parse crm_mon XML into quorum, per-node states, and resource placement.

    Returns ``{quorate: bool, nodes: {name: {online, standby, unclean}},
    resources: {id: {state, node}}}``. Resource placement is scoped to every resource *group* (the
    rows the cockpit shows); top-level STONITH ``fence_*`` resources are deliberately excluded —
    they live outside any group, and fencing is reported separately from stonith_admin history.
    """
    root = ET.fromstring(xml_text)  # noqa: S314  # locally-run crm_mon output, not untrusted input
    dc = root.find("./summary/current_dc")
    quorate = dc is not None and dc.get("with_quorum") == "true"

    nodes: dict[str, dict[str, bool]] = {}
    for n in root.findall("./nodes/node"):
        nodes[n.get("name", "")] = {
            "online": n.get("online") == "true",
            "standby": n.get("standby") == "true",
            "unclean": n.get("unclean") == "true",
        }

    resources: dict[str, dict[str, Any]] = {}
    for r in root.findall(".//group//resource"):
        rid = r.get("id", "")
        held = r.find("./node")
        resources[rid] = {
            "state": r.get("role", "Unknown"),
            "node": held.get("name") if held is not None else None,
        }
    return {"quorate": quorate, "nodes": nodes, "resources": resources}


def parse_stonith(text: str) -> dict[str, int]:
    """``stonith_admin --history '*'`` -> ``{node: fence_action_count}``; empty == clean."""
    counts: dict[str, int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if " was reset " in line or " was fenced " in line:
            node = line.split(" ", 1)[0]
            counts[node] = counts.get(node, 0) + 1
    return counts


def parse_iscsi(text: str) -> int:
    """``iscsiadm -m session`` -> count of active sessions (lines starting with a transport)."""
    return sum(1 for raw in text.splitlines() if raw.strip().startswith(("tcp:", "iser:")))


def parse_daemons(text: str, units: list[str]) -> dict[str, bool]:
    """One ``systemctl is-active`` line per unit (same order) -> ``{unit: is_active}``.

    Fewer lines than units (a truncated read) reads the missing units as down — fail-loud, never a
    silent green.
    """
    lines = text.splitlines()
    return {
        unit: (lines[i].strip() == "active" if i < len(lines) else False)
        for i, unit in enumerate(units)
    }


def render_cluster_state_prom(
    *,
    node: str,
    crm: dict[str, Any] | None,
    stonith: dict[str, int] | None,
    iscsi: int | None,
    daemons: dict[str, bool],
    drbd: dict[str, Any] | None,
    now: int,
    fresh_sources: tuple[str, ...],
) -> str:
    """Project parsed probe results into node_exporter textfile lines (label ``node=<self>``)."""
    lines: list[str] = []

    if crm is not None:
        lines.append(_m("cluster_quorate", {"node": node}, 1 if crm["quorate"] else 0))
        for member, st in crm["nodes"].items():
            base = {"node": node, "member": member}
            lines.append(_m("cluster_node_online", base, 1 if st["online"] else 0))
            lines.append(_m("cluster_node_unclean", base, 1 if st["unclean"] else 0))
        for rid, r in crm["resources"].items():
            rbase = {"node": node, "resource": rid}
            started = 1 if r["state"] == "Started" else 0
            lines.append(_m("cluster_resource_started", rbase, started))
            if r["node"]:
                lines.append(_m("cluster_resource_owner", {**rbase, "holder": r["node"]}, 1))
        # Fence baseline: every known member reads 0 (clean -> green) unless stonith history shows
        # events for it (-> red). Tied to the crm member set so a clean cluster is not a column of
        # grey no-data.
        fences = stonith or {}
        for member in crm["nodes"]:
            fc = {"node": node, "member": member}
            lines.append(_m("cluster_fence_count", fc, fences.get(member, 0)))

    if iscsi is not None:
        lines.append(_m("cluster_iscsi_sessions", {"node": node}, iscsi))

    for unit, up in daemons.items():
        lines.append(_m("cluster_daemon_up", {"node": node, "unit": unit}, 1 if up else 0))

    if drbd is not None:
        for res, d in drbd.items():
            rbase = {"node": node, "resource": res}
            for kind in ("role", "disk", "conn"):
                lines.append(_m(f"cluster_drbd_{kind}", {**rbase, kind: d[kind]}, 1))
            if d["resync_pct"] is not None:
                lines.append(_m("cluster_drbd_resync_pct", rbase, d["resync_pct"]))
            if d["out_of_sync_bytes"] is not None:
                lines.append(_m("cluster_drbd_out_of_sync_bytes", rbase, d["out_of_sync_bytes"]))

    lines.extend(last_write_lines(node, fresh_sources, now))

    return "\n".join(lines) + "\n"


def collect(node: str, now: int) -> str:
    """Run every probe source bounded and render the textfile body; absent sources go STALE."""
    fresh: list[str] = []

    crm = None
    raw = probe(*_COMMANDS["crm"])
    if raw is not None:
        crm = parse_crm(raw)
        fresh.append("crm")

    stonith = None
    raw = probe(*_COMMANDS["stonith"])
    if raw is not None:
        stonith = parse_stonith(raw)
        fresh.append("stonith")

    iscsi = None
    raw = probe(*_COMMANDS["iscsi"])
    if raw is not None:
        iscsi = parse_iscsi(raw)
        fresh.append("iscsi")

    drbd = None
    raw = probe(*_COMMANDS["drbd"])
    if raw is not None:
        drbd = parse_drbd(raw)
        fresh.append("drbd")

    daemons: dict[str, bool] = {}
    # is-active exits non-zero for an inactive/failed unit — a valid "down" reading, not a probe
    # failure — so keep stdout regardless of rc; only a timeout (None) marks the source STALE.
    raw = probe(["systemctl", "is-active", *_DAEMON_UNITS], 2, ignore_rc=True)
    if raw is not None:
        daemons = parse_daemons(raw, list(_DAEMON_UNITS))
        fresh.append("daemons")

    return render_cluster_state_prom(
        node=node,
        crm=crm,
        stonith=stonith,
        iscsi=iscsi,
        daemons=daemons,
        drbd=drbd,
        now=now,
        fresh_sources=tuple(fresh),
    )


def run(profile: Profile, *, node: str | None = None, now: int | None = None) -> Path:
    """Collect against ``profile`` and atomically write the textfile; return its path.

    ``node`` defaults to this host's name (the label every series carries), and ``now`` to the wall
    clock — both overridable for deterministic tests. Only ``profile.textfile_dir`` is read: the
    Pacemaker collector sources resource identities from crm_mon, so it needs no queue-manager name.
    """
    resolved_node = node if node is not None else os.uname().nodename
    resolved_now = now if now is not None else int(time.time())
    body = collect(resolved_node, resolved_now)
    out = profile.textfile_dir / _TEXTFILE_NAME
    write_textfile(out, body)
    return out
