"""RDQM (Replicated Data Queue Manager) cluster-state collector — a contract-preserving
extraction of the lab's ``rdqmstate.py``.

Stdlib-only, so this file deploys verbatim onto the RDQM nodes and is also imported by the unit
tests. RDQM owns its Pacemaker + DRBD behind ``rdqmadm``, so the collector probes the RDQM-native
``rdqmstatus`` (not the raw ``crm_mon`` a standalone Pacemaker node would) plus ``drbdsetup status``
for the replicated volumes and, optionally, ``crm_mon`` for the resource-manager view that exposes a
banned/failed QM ``rdqmstatus`` alone cannot see. Pure parse functions turn that output into rows;
:func:`render_rdqm_state_prom` projects rows into node_exporter textfile lines; :func:`run` shells
each source out bounded + non-blocking (timeout -> no fresh sample -> the cell reads STALE) and
writes the textfile atomically.

Three deliberate reuses keep this collector consistent with its siblings:

- The **DRBD parser is imported** from :mod:`mqro.collectors.drbd` (#15), not re-extracted here, so
  the RDQM and Pacemaker collectors project byte-identical ``cluster_drbd_*`` series.
- The **render/probe/textfile helpers** come from :mod:`mqro.collectors.base`, so every collector
  formats and writes the same way. ``rdqmstatus`` writes its report to STDERR under the systemd
  timer, so its probe runs with ``merge_stderr`` (see :func:`base.probe`).
- It emits the same ``cluster_*`` shape as the sibling collectors (so a dashboard built against the
  lab reads this output unchanged); RDQM-only facets get ``cluster_rdqm_*`` names.

Two lab hardcodes are dropped so this runs against any deployment: the queue-manager name (was a
baked ``RDQMAPP``) comes from ``profile.qm``, and the output path from ``profile.textfile_dir`` (was
an assumed ``/var/lib/...`` path).
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
_TEXTFILE_NAME = "mqro_rdqm_state.prom"

# Pacemaker INFINITY: a resource whose fail-count reaches this is banned from running on that node
# (the migration threshold is reached). crm_mon prints it as 1000000 (or "INFINITY").
_INFINITY = 1000000
# crm_mon role -> instances-matrix state code: Started/Promoted = active leader (2, green),
# Unpromoted = healthy replica (1, blue), anything else (Stopped/None) = 0 (neutral).
_PM_CODE = {"Started": 2, "Promoted": 2, "Master": 2, "Unpromoted": 1, "Slave": 1}
# The pacemaker resources of the QM stack the board surfaces per node (the technology layer rdqmadm
# wraps): the QM itself, the HA + DR DRBD clones, and the floating-IP resource.
_PM_RESOURCES = ("qmrdqm", "p_drbd_qmrdqm", "p_drbd_dr_qmrdqm", "p_ip_qmrdqm")

# HA role -> numeric code for the instances-matrix role cell. Primary (runs the QM) is the healthy
# green leader; Secondary is a healthy standby (blue); anything else codes 0=Unknown.
_ROLE_CODE = {"Primary": 2, "Secondary": 1}
# DR role -> code for the per-site LIVE/RECOVERY chip: the DR-primary site is LIVE (2/green), the
# DR-secondary site is RECOVERY (3/yellow). Flips on an rdqmdr cutover.
_DR_ROLE_CODE = {"Primary": 2, "Secondary": 3}

# rdqmstatus field label -> the parsed key the renderer reads. Only the local (first) block carries
# the rich fields; footer blocks carry just the per-member HA status.
_LOCAL_FIELDS = {
    "Queue manager status": "qm_status",
    "HA role": "ha_role",
    "HA current location": "ha_current_location",
    "HA preferred location": "ha_preferred_location",
    "HA floating IP address": "floating_ip",
    "HA floating IP interface": "floating_ip_interface",
    "DR role": "dr_role",
    "DR status": "dr_status",
}

# source -> (argv, timeout seconds). rdqmstatus + drbdsetup + crm_mon all run as root (the unit runs
# as root, like the Pacemaker collector); timeouts sit well under a typical 5s scrape tick.
_RDQMSTATUS_BIN = "/opt/mqm/bin/rdqmstatus"


def parse_rdqmstatus(text: str) -> dict[str, Any]:
    """Parse ``rdqmstatus -m <qm>`` into the local node's HA/DR view + per-member HA status.

    The output is a series of ``Node:``-led blocks separated by blank lines. The FIRST block is the
    local node (rich: QM status, HA role/status/location, floating IP, DR fields); the remaining
    blocks are the HA peers, carrying just ``HA status``. Every block (local + peers) contributes a
    per-member HA status row; the local block additionally fills the summary fields. Unseen fields
    read None (fail-loud: nothing fabricated).
    """
    blocks: list[dict[str, str]] = []
    cur: dict[str, str] | None = None
    for raw in text.splitlines():
        if not raw.strip() or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key, value = key.strip(), value.strip()
        if key == "Node":
            cur = {"node": value}
            blocks.append(cur)
        elif cur is not None:  # a field line within the current block (ignore stray preamble)
            cur[key] = value

    local = blocks[0] if blocks else {}
    summary: dict[str, Any] = {field: local.get(label) for label, field in _LOCAL_FIELDS.items()}
    return {
        "node": local.get("node"),
        "ha_status": local.get("HA status", "Unknown"),
        **summary,
        "members": {b["node"]: {"ha_status": b.get("HA status", "Unknown")} for b in blocks},
    }


def _parse_failcount(fc: str | None) -> int:
    """A crm_mon fail-count attribute -> int. None/garbage -> 0 (fail-loud: a node whose failcount
    we can't read is assumed startable, never silently banned); "INFINITY" -> the pacemaker
    INFINITY sentinel."""
    if fc is None:
        return 0
    if fc == "INFINITY":
        return _INFINITY
    return int(fc) if fc.lstrip("-").isdigit() else 0


def parse_crm(text: str) -> dict[str, Any]:
    """Parse ``crm_mon --one-shot --output-as=xml`` into the pacemaker view of the QM stack:
    per-node online state, per-resource placement/role, and the QM's per-node fail-count.

    This is the resource-manager layer that rdqmadm wraps — rdqmstatus reports HA/DRBD health but
    NOT whether pacemaker can actually start the QM, so a banned/failed resource is only visible
    here. Returns ``{nodes:{name:{online}}, roles:{id:{node:role}}, failcount:{node:int}}``.
    A Stopped resource has no node placement and so no roles entry.
    """
    root = ET.fromstring(text)  # noqa: S314  # locally-run crm_mon output, not untrusted input
    nodes = {
        n.get("name", ""): {"online": n.get("online") == "true"}
        for n in root.findall("./nodes/node")
    }

    roles: dict[str, dict[str, str]] = {}
    res_parent = root.find("resources")
    if res_parent is not None:
        # iter() flattens clone/group wrappers to their per-node <resource> instances, so each DRBD
        # clone instance contributes one (node -> role) entry under its primitive id.
        for res in res_parent.iter("resource"):
            held = res.find("node")
            if held is not None:  # a Stopped instance has no <node> placement -> skip
                roles.setdefault(res.get("id", ""), {})[held.get("name", "")] = res.get("role", "")

    failcount: dict[str, int] = {}
    for n in root.findall(".//node_history/node"):
        for rh in n.findall("resource_history"):
            if rh.get("id") == "qmrdqm":
                failcount[n.get("name", "")] = _parse_failcount(rh.get("fail-count"))

    return {"nodes": nodes, "roles": roles, "failcount": failcount}


def _resolve(location: str, node: str) -> str:
    """Resolve an HA location to a node name: ``rdqmstatus`` prints ``This node`` for the local
    node, else the peer's name. Resolving here makes every node's owner series agree."""
    return node if location == "This node" else location


def render_rdqm_state_prom(
    *,
    node: str,
    qm: str,
    status: dict[str, Any] | None,
    drbd: dict[str, Any] | None,
    crm: dict[str, Any] | None = None,
    now: int,
    fresh_sources: tuple[str, ...],
) -> str:
    """Project parsed rdqmstatus + drbd + crm_mon results into node_exporter textfile lines
    (label ``node=<self>``)."""
    lines: list[str] = []

    if status is not None:
        ha_ok = 1 if status["ha_status"] == "Normal" else 0
        lines.append(_m("cluster_quorate", {"node": node}, ha_ok))
        lines.append(_m("cluster_rdqm_ha_status_ok", {"node": node}, ha_ok))
        lines.append(_m("cluster_rdqm_ha_status", {"node": node, "status": status["ha_status"]}, 1))

        role, member = status["ha_role"], {"node": node, "member": node}
        if role is not None:
            lines.append(_m("cluster_rdqm_role", {**member, "role": role}, 1))
        lines.append(_m("cluster_rdqm_role_code", member, _ROLE_CODE.get(role, 0)))
        running = 1 if status["qm_status"] == "Running" else 0
        lines.append(_m("cluster_rdqm_qm_running", member, running))

        for mname, st in status["members"].items():
            base = {"node": node, "member": mname}
            lines.append(_m("cluster_node_online", base, 1 if st["ha_status"] == "Normal" else 0))
            lines.append(_m("cluster_rdqm_member_status", {**base, "status": st["ha_status"]}, 1))

        # The floating IP and resource ownership belong ONLY to the node actually RUNNING the QM.
        # In a DR pair both sites' HA primaries report "HA current location: This node" and a
        # configured VIP, but only the live node serves them — gating on `running` keeps the board
        # from showing the QM "running on" two nodes with two floating IPs (#287).
        if status["floating_ip"] is not None and running:
            fl = {
                "node": node,
                "ip": status["floating_ip"],
                "interface": status["floating_ip_interface"],
            }
            lines.append(_m("cluster_rdqm_floating_ip", fl, 1))

        cur_loc = status["ha_current_location"]
        if cur_loc is not None:
            holder = _resolve(cur_loc, node)
            # location is always published (the failback signal); ownership only when running
            lines.append(
                _m("cluster_rdqm_location", {"node": node, "kind": "current", "holder": holder}, 1)
            )
            if running:
                owner = {"node": node, "resource": qm, "holder": holder}
                lines.append(_m("cluster_resource_owner", owner, 1))
        pref_loc = status["ha_preferred_location"]
        if pref_loc is not None:
            pref = {"node": node, "kind": "preferred", "holder": _resolve(pref_loc, node)}
            lines.append(_m("cluster_rdqm_location", pref, 1))

        dr_role = status["dr_role"]
        if dr_role is not None:
            lines.append(_m("cluster_rdqm_dr_role", {"node": node, "role": dr_role}, 1))
            dr_code = _DR_ROLE_CODE.get(dr_role, 0)
            lines.append(_m("cluster_rdqm_dr_role_code", {"node": node}, dr_code))
        dr_status = status["dr_status"]
        # Non-QM-running nodes print `DR status: See <primary>` — a pointer, not a real status. Skip
        # it so it can't falsely drag the DR card to Degraded; only the authoritative node's real
        # status (Normal/Disconnected/...) drives the DR health (#287).
        if dr_status is not None and not dr_status.startswith("See "):
            lines.append(_m("cluster_rdqm_dr_status", {"node": node, "status": dr_status}, 1))
            dr_ok = 1 if dr_status == "Normal" else 0
            lines.append(_m("cluster_rdqm_dr_status_ok", {"node": node}, dr_ok))

    if drbd is not None:
        for res, d in drbd.items():
            rbase = {"node": node, "resource": res}
            for kind in ("role", "disk", "conn"):
                lines.append(_m(f"cluster_drbd_{kind}", {**rbase, kind: d[kind]}, 1))
            lines.append(_m("cluster_drbd_resync_pct", rbase, d["resync_pct"]))
            lines.append(_m("cluster_drbd_out_of_sync_bytes", rbase, d["out_of_sync_bytes"]))

    if crm is not None:
        # The pacemaker layer: per-node online + the QM stack's per-resource state, plus the QM
        # fail-count and a derived "startable" (fail-count below INFINITY). This is what exposes a
        # banned/failed QM that rdqmstatus reports as HA-Normal (#287).
        for cmember, st in crm["nodes"].items():
            base = {"node": node, "member": cmember}
            lines.append(_m("cluster_rdqm_pm_online", base, 1 if st["online"] else 0))
            for res in _PM_RESOURCES:
                pm_role = crm["roles"].get(res, {}).get(cmember)
                state = {**base, "resource": res}
                lines.append(_m("cluster_rdqm_pm_state", state, _PM_CODE.get(pm_role, 0)))
            fc = crm["failcount"].get(cmember, 0)
            startable = fc < _INFINITY
            lines.append(_m("cluster_rdqm_failcount", base, fc))
            lines.append(_m("cluster_rdqm_qm_startable", base, 1 if startable else 0))
            # a usable host is BOTH pacemaker-online AND able to run the QM: a node online but
            # banned can host nothing, so it must not read a green "online" (#287).
            ready = 1 if st["online"] and startable else 0
            lines.append(_m("cluster_rdqm_node_ready", base, ready))

    lines.extend(last_write_lines(node, fresh_sources, now))

    return "\n".join(lines) + "\n"


def _commands(qm: str) -> dict[str, tuple[list[str], int]]:
    """source -> (argv, timeout). rdqmstatus + drbdsetup + crm_mon all run as root; timeouts are
    well under the 5s tick."""
    return {
        "rdqmstatus": ([_RDQMSTATUS_BIN, "-m", qm], 3),
        "drbd": (["drbdsetup", "status", "--verbose", "--statistics"], 2),
        "crm": (["crm_mon", "--one-shot", "--output-as=xml"], 3),
    }


def collect(node: str, qm: str, now: int) -> str:
    """Run the rdqmstatus + drbd + crm_mon probes and render the textfile body; absent sources go
    STALE."""
    cmds = _commands(qm)
    fresh: list[str] = []

    status = None
    raw = probe(*cmds["rdqmstatus"], merge_stderr=True)  # rdqmstatus reports on stderr
    if raw is not None:
        status = parse_rdqmstatus(raw)
        fresh.append("rdqmstatus")

    drbd = None
    raw = probe(*cmds["drbd"])
    if raw is not None:
        drbd = parse_drbd(raw)
        fresh.append("drbd")

    crm = None
    raw = probe(*cmds["crm"])
    if raw is not None:
        crm = parse_crm(raw)
        fresh.append("crm")

    return render_rdqm_state_prom(
        node=node, qm=qm, status=status, drbd=drbd, crm=crm, now=now, fresh_sources=tuple(fresh)
    )


def run(profile: Profile, *, node: str | None = None, now: int | None = None) -> Path:
    """Collect against ``profile`` and atomically write the textfile; return its path.

    ``node`` defaults to this host's name (the label every series carries), and ``now`` to the wall
    clock — both overridable for deterministic tests. ``profile.qm`` names the queue manager passed
    to ``rdqmstatus -m`` and worn by ``cluster_resource_owner``.
    """
    resolved_node = node if node is not None else os.uname().nodename
    resolved_now = now if now is not None else int(time.time())
    body = collect(resolved_node, profile.qm, resolved_now)
    out = profile.textfile_dir / _TEXTFILE_NAME
    write_textfile(out, body)
    return out
