"""Native-HA cluster-state collector — a contract-preserving extraction of the lab's
``nativehastate.py``.

Stdlib-only, so this file deploys verbatim onto the Native-HA nodes and is also imported by
the unit tests. Pure parse functions turn ``dspmq -o nativeha -x``/``-g`` output into rows;
:func:`render_nativeha_state_prom` projects rows into node_exporter textfile lines;
:func:`run` shells each source out bounded + non-blocking (timeout -> no fresh sample -> the
cell reads STALE) and writes the textfile atomically.

Emits the same ``cluster_*`` / ``cluster_nha_*`` families (names, labels, and semantics) as
the lab, byte-for-byte for the same input — a dashboard built against the lab reads this
collector's output unchanged.
"""

from __future__ import annotations

import os
import re
import time
from typing import TYPE_CHECKING, Any

from mqro.collectors.base import (
    last_write_lines,
    probe,
    write_textfile,
)
from mqro.collectors.base import (
    metric_line as _m,
)

if TYPE_CHECKING:
    from pathlib import Path

    from mqro.config import Profile

_FIELD = re.compile(r"(\w+)\(([^)]*)\)")

# The node_exporter textfile this collector writes into the profile's textfile directory.
_TEXTFILE_NAME = "mqro_nativeha_state.prom"

# Numeric role code for the cockpit instances-matrix cell (the colour-cell machinery is
# numeric). The Live group's leader reports ROLE(Active) (running the QM); the Recovery
# group's leader reports ROLE(Leader) (applying CRR replication — healthy, NOT a problem).
# Both are healthy leaders; Replica is a healthy follower; anything else (down/transitioning)
# falls through to 0=Unknown.
_ROLE_CODE = {"Active": 2, "Leader": 3, "Replica": 1}


def _fields(line: str) -> dict[str, str]:
    """All KEY(value) tokens on a line -> {KEY: value}. Values that themselves contain
    parens (GRPADDR) aren't read by any metric, so the naive scan is harmless."""
    return dict(_FIELD.findall(line))


def _int_or_none(value: str | None) -> int | None:
    """Parse an MQ numeric field, tolerating 'Unknown'/empty/missing. dspmq reports
    BACKLOG(Unknown) (and INSYNC(Unknown)) for a recovery group still waiting to be
    rebased by the live group — a legitimate, transient CRR state. The collector must
    NEVER raise on a degraded-but-valid reading: one unparseable field would abort the
    whole textfile render and blank every panel for that node ('no data') instead of
    showing it degraded (#390)."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_nativeha_x(text: str) -> dict[str, Any]:
    """Parse `dspmq -m <qm> -o nativeha -x` into quorum + group role + per-instance state.

    The leading QMNAME summary line carries QUORUM(x/y) + GRPNAME/GRPROLE (but no HASTATUS);
    the indented lines are one per instance (INSTANCE/ROLE/INSYNC/HASTATUS/...). INSYNC(yes)
    -> True. The summary line is consumed for quorum + group role only, never as an instance
    row (the per-instance state comes from the indented lines, which carry HASTATUS).
    """
    summary: dict[str, Any] = {
        "quorum_current": None,
        "quorum_total": None,
        "group_role": None,
    }
    instances: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        f = _fields(line)
        if not f:
            continue
        if "QUORUM" in f:
            cur, _, total = f["QUORUM"].partition("/")
            summary["quorum_current"] = _int_or_none(cur)
            summary["quorum_total"] = _int_or_none(total)
            summary["group_role"] = f.get("GRPROLE")
            continue
        if "INSTANCE" in f and "ROLE" in f:
            instances[f["INSTANCE"]] = {
                "role": f["ROLE"],
                "insync": f.get("INSYNC") == "yes",
                "hastatus": f.get("HASTATUS", "Unknown"),
            }
    return {**summary, "instances": instances}


def _yn(f: dict[str, str], key: str) -> bool | None:
    """A yes/no field as bool, or None when the field is absent (the live group does not
    report CONNGRP/INSYNC — those are reported only on the recovery group line)."""
    return f[key] == "yes" if key in f else None


def parse_nativeha_g(text: str) -> dict[str, dict[str, Any]]:
    """Parse `dspmq -m <qm> -o nativeha -g` (CRR) into {group_name: {role,status,connected,
    insync,backlog}}, keyed by GRPNAME.

    The output is led by the same QMNAME summary line as -x (skipped here — it carries
    QUORUM), then one line per group. Both groups report GRPROLE + GRSTATUS; only the
    recovery group line carries CONNGRP/INSYNC/BACKLOG (the live group's cross-region facets
    are absent, so they read None and are omitted downstream rather than faked). BACKLOG is a
    message count, never seconds.
    """
    groups: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        f = _fields(line)
        if "GRPNAME" not in f or "QUORUM" in f:  # skip non-group lines + the QMNAME summary
            continue
        groups[f["GRPNAME"]] = {
            "role": f.get("GRPROLE", "Unknown"),
            "status": f.get("GRSTATUS", "Unknown"),
            "connected": _yn(f, "CONNGRP"),
            "insync": _yn(f, "INSYNC"),
            "backlog": _int_or_none(f.get("BACKLOG")),
        }
    return groups


def render_nativeha_state_prom(
    *,
    node: str,
    qm: str,
    hax: dict[str, Any] | None,
    grp: dict[str, dict[str, Any]] | None,
    now: int,
    fresh_sources: tuple[str, ...],
) -> str:
    """Project parsed dspmq results into node_exporter textfile lines (label node=<self>)."""
    lines: list[str] = []

    if hax is not None:
        cur, total = hax["quorum_current"], hax["quorum_total"]
        if cur is not None and total is not None:
            majority = total // 2 + 1
            lines.append(_m("cluster_quorate", {"node": node}, 1 if cur >= majority else 0))
            lines.append(_m("cluster_nha_quorum", {"node": node}, cur))
        for member, st in hax["instances"].items():
            base = {"node": node, "member": member}
            lines.append(_m("cluster_node_online", base, 0 if st["role"] == "Unknown" else 1))
            lines.append(_m("cluster_nha_role", {**base, "role": st["role"]}, 1))
            lines.append(_m("cluster_nha_role_code", base, _ROLE_CODE.get(st["role"], 0)))
            lines.append(_m("cluster_nha_insync", base, 1 if st["insync"] else 0))
            lines.append(_m("cluster_nha_hastatus", {**base, "status": st["hastatus"]}, 1))
            ha_ok = 1 if st["hastatus"] == "Normal" else 0
            lines.append(_m("cluster_nha_hastatus_ok", base, ha_ok))
            if st["role"] == "Active":
                owner = {"node": node, "resource": qm, "holder": member}
                lines.append(_m("cluster_resource_owner", owner, 1))

    if grp is not None:
        for name, g in grp.items():
            gbase = {"node": node, "group": name}
            lines.append(_m("cluster_nha_group_role", {**gbase, "role": g["role"]}, 1))
            lines.append(_m("cluster_nha_group_status", {**gbase, "status": g["status"]}, 1))
            # CRR facets are reported only on the recovery group line; omit (never fake)
            # them for the live group, where dspmq does not report them.
            if g["connected"] is not None:
                lines.append(_m("cluster_nha_connected", gbase, 1 if g["connected"] else 0))
            if g["insync"] is not None:
                lines.append(_m("cluster_nha_group_insync", gbase, 1 if g["insync"] else 0))
            if g["backlog"] is not None:
                lines.append(_m("cluster_nha_group_backlog", gbase, g["backlog"]))

    lines.extend(last_write_lines(node, fresh_sources, now))

    return "\n".join(lines) + "\n"


def _commands(qm: str) -> dict[str, tuple[list[str], int]]:
    """source -> (argv, timeout). dspmq must run as the mqm user (matching the arm's
    qm-status verb), so each probe shells through `su - mqm -c`; the absolute dspmq path
    sidesteps any dependence on mqm's PATH. Timeouts are well under the 5s tick."""
    dspmq = f"/opt/mqm/bin/dspmq -m {qm} -o nativeha"
    return {
        "nativeha_x": (["su", "-", "mqm", "-c", f"{dspmq} -x"], 3),
        "nativeha_g": (["su", "-", "mqm", "-c", f"{dspmq} -g"], 3),
    }


def collect(node: str, qm: str, now: int) -> str:
    """Run both dspmq probes and render the textfile body."""
    cmds = _commands(qm)
    fresh: list[str] = []
    raw_x = probe(*cmds["nativeha_x"])
    hax = None
    if raw_x is not None:
        hax = parse_nativeha_x(raw_x)
        fresh.append("nativeha_x")
    raw_g = probe(*cmds["nativeha_g"])
    grp = None
    if raw_g is not None:
        grp = parse_nativeha_g(raw_g)
        fresh.append("nativeha_g")
    return render_nativeha_state_prom(
        node=node, qm=qm, hax=hax, grp=grp, now=now, fresh_sources=tuple(fresh)
    )


def run(profile: Profile, *, node: str | None = None, now: int | None = None) -> Path:
    """Collect against ``profile`` and atomically write the textfile; return its path.

    ``node`` defaults to this host's name (the label every series carries), and ``now`` to
    the wall clock — both overridable for deterministic tests.
    """
    resolved_node = node if node is not None else os.uname().nodename
    resolved_now = now if now is not None else int(time.time())
    body = collect(resolved_node, profile.qm, resolved_now)
    out = profile.textfile_dir / _TEXTFILE_NAME
    write_textfile(out, body)
    return out
