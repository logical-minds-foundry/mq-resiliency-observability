"""The Grafana panel toolkit — data in → panel/dashboard dicts out, no I/O.

Ported from the lab's ``clusterboard`` primitives (one toolkit, several board layers). The single
de-hardcoding change: every builder takes its datasource UID as an argument, so no ``"prometheus"``
/ ``"loki"`` literal survives. Everything here is a pure function of its arguments, so a render is
deterministic — the property the dashboard tests assert.

The stat/timeseries/table/logs/state-timeline builders, the row-key normalizer, and the full
colour-mapping taxonomy are ported here. The ``role`` / ``rdqm_role`` / ``qm_running`` /
``node_ready`` / ``pm_state`` / ``failcount`` / ``drbd_oos`` families and the background
:func:`badge` helper (the Native-HA / RDQM site chips) landed with those boards; every mapping key
and helper here is exercised by a shipped board, so no dead branch dilutes the 100%-branch bar.
"""

from __future__ import annotations

from typing import Any

# (title, promql, mapping_kind) — one normalized column of a node×component matrix.
Column = tuple[str, str, str]

# Colour taxonomy: green = live/active-good; blue = healthy but not the live-active one (the
# "alternate green" — Replica, Secondary, Recovery standby); yellow = a soft warning; red = bad.
_GREEN, _RED = "green", "red"
_BLUE, _YELLOW = "blue", "yellow"
_REFIDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Compact value-font (px) for single-row status bands — caps Grafana's auto-fit so the tiles do
# not waste vertical space with huge numbers.
_COMPACT_VALUE_SIZE = 22

# Colour mappings the boards use. 1→green/0→red (up), 0→green/≥1→red (clean0), ≥1→green/0→red
# (sessions); plus the HA/DR families: coded HA roles (Native HA `role`, RDQM `rdqm_role`),
# QM-running (0 is a healthy standby, blue — not a failure), node-ready (online AND startable),
# the Pacemaker resource-state code (`pm_state`), the fail-count ladder (ok/failing/BANNED), and
# the extent-tolerant DRBD out-of-sync mapping (`drbd_oos`).
_MAPPINGS: dict[str, list[dict[str, Any]]] = {
    "up": [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "down", "index": 0},
                "1": {"color": _GREEN, "text": "up", "index": 1},
            },
        },
    ],
    "clean0": [
        {"type": "value", "options": {"0": {"color": _GREEN, "text": "ok", "index": 0}}},
        {
            "type": "range",
            "options": {
                "from": 1,
                "to": 9999,
                "result": {"color": _RED, "text": "!", "index": 1},
            },
        },
    ],
    "sessions": [
        {"type": "value", "options": {"0": {"color": _RED, "text": "none", "index": 0}}},
        {
            "type": "range",
            "options": {
                "from": 1,
                "to": 9999,
                "result": {"color": _GREEN, "text": "ok", "index": 1},
            },
        },
    ],
    # Native-HA role code → coloured text. Active (2) runs the QM (green); Leader (3) / Replica (1)
    # are healthy standbys (blue — not a warning); only a genuinely down/unknown instance is red.
    "role": [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "Unknown", "index": 0},
                "1": {"color": _BLUE, "text": "Replica", "index": 1},
                "2": {"color": _GREEN, "text": "Active", "index": 2},
                "3": {"color": _BLUE, "text": "Leader", "index": 3},
            },
        },
    ],
    # RDQM HA role code → coloured text. Primary (2) runs the QM (green); Secondary (1) is the
    # healthy DRBD standby (blue); only a down/unknown instance is red.
    "rdqm_role": [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "Unknown", "index": 0},
                "1": {"color": _BLUE, "text": "Secondary", "index": 1},
                "2": {"color": _GREEN, "text": "Primary", "index": 2},
            },
        },
    ],
    # QM-running: 1 → green "running"; 0 is a healthy STANDBY node (the QM is only ever live on
    # one node), shown neutral blue — NOT red "down", which would falsely read as failure on every
    # standby.
    "qm_running": [
        {
            "type": "value",
            "options": {
                "0": {"color": _BLUE, "text": "standby", "index": 0},
                "1": {"color": _GREEN, "text": "✓ running", "index": 1},
            },
        },
    ],
    # Node ready: 1 = Pacemaker-online AND able to run the QM → green "ready"; 0 = online-but-
    # banned (or offline) → red "blocked". A node that can host nothing must not read green.
    "node_ready": [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "blocked", "index": 0},
                "1": {"color": _GREEN, "text": "ready", "index": 1},
            },
        },
    ],
    # Pacemaker resource-state code → text. 2 = Started/Promoted (active, green); 1 = Unpromoted
    # (healthy replica, blue); 0 = Stopped/absent — NEUTRAL grey, not an alarm (a stopped resource
    # on a standby is normal; the fail-count column is what flags a real ban).
    "pm_state": [
        {
            "type": "value",
            "options": {
                "0": {"color": "#5a6168", "text": "stopped", "index": 0},
                "1": {"color": _BLUE, "text": "replica", "index": 1},
                "2": {"color": _GREEN, "text": "active", "index": 2},
            },
        },
    ],
    # Pacemaker fail-count → 0 green "ok"; a soft failure (1..<INFINITY) amber "failing"; the
    # Pacemaker INFINITY sentinel (1000000 = migration threshold reached) → red "BANNED".
    "failcount": [
        {"type": "value", "options": {"0": {"color": _GREEN, "text": "ok", "index": 0}}},
        {
            "type": "range",
            "options": {
                "from": 1,
                "to": 999999,
                "result": {"color": _YELLOW, "text": "failing", "index": 1},
            },
        },
        {
            "type": "range",
            "options": {
                "from": 1000000,
                "to": 1e12,
                "result": {"color": _RED, "text": "BANNED", "index": 2},
            },
        },
    ],
    # DRBD out-of-sync bytes, extent-tolerant: 0..one 4 KiB extent → green (a benign sub-extent
    # secondary↔secondary delta is normal and must read green); a real backlog (≥ one extent) →
    # red. The honest byte value stays visible in both ranges.
    "drbd_oos": [
        {
            "type": "range",
            "options": {"from": 0, "to": 4095, "result": {"color": _GREEN, "index": 0}},
        },
        {
            "type": "range",
            "options": {"from": 4096, "to": 1e12, "result": {"color": _RED, "index": 1}},
        },
    ],
}

# A truly-null series must never read healthy: STALE wins on no-data (fail-loud).
_STALE_MAP: dict[str, Any] = {
    "type": "special",
    "options": {"match": "null", "result": {"color": "text", "text": "STALE", "index": 9}},
}

# A first-class integrity light: 0 → green "✓ integrity", ≥1 → red "⚠ HAZARD", no-data → STALE.
# Shared by every cockpit's status band (the hazard EXPR is arm-specific; this vocabulary is not).
INTEGRITY_MAPS: list[dict[str, Any]] = [
    {"type": "value", "options": {"0": {"color": _GREEN, "text": "✓ integrity", "index": 0}}},
    {
        "type": "range",
        "options": {
            "from": 1,
            "to": 9999,
            "result": {"color": _RED, "text": "⚠ HAZARD", "index": 1},
        },
    },
    _STALE_MAP,
]

# MQ channel/QM status squash: -1 no-status (grey) · 0 stopped (red) · 1 transitioning (yellow) ·
# 2 running (green).
_STATUS_MAP: list[dict[str, Any]] = [
    {
        "type": "value",
        "options": {
            "-1": {"text": "No status", "color": "grey", "index": 0},
            "0": {"text": "Stopped", "color": "red", "index": 1},
            "1": {"text": "Transitioning", "color": "yellow", "index": 2},
            "2": {"text": "Running", "color": "green", "index": 3},
        },
    },
]

_WARN_REGEX = "(?i)warn|error|fail|fenc|crit|alert|emerg"


def _ds(uid: str) -> dict[str, str]:
    """A Prometheus datasource reference."""
    return {"type": "prometheus", "uid": uid}


def _qm_status_expr(qm: str) -> str:
    """The object-driven QM status expr: no live status reads -1 (No status), never blank."""
    return f'max(ibmmq_qmgr_status{{qmgr="{qm}"}}) or vector(-1)'


def banner(content: str, y: int) -> dict[str, Any]:
    """A transparent full-width markdown title banner."""
    return {
        "type": "text",
        "title": "",
        "transparent": True,
        "gridPos": {"h": 2, "w": 24, "x": 0, "y": y},
        "options": {"mode": "markdown", "content": content},
    }


def row_header(title: str, y: int) -> dict[str, Any]:
    """A section header row (uncollapsed)."""
    return {
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "panels": [],
    }


def stat(
    title: str,
    expr: str,
    ds_uid: str,
    x: int,
    y: int,
    *,
    mappings: list[dict[str, Any]] | None = None,
    unit: str | None = None,
    text_mode: str = "value",
    name_label: str = "holder",
    w: int = 6,
    h: int = 4,
    value_size: int | None = None,
) -> dict[str, Any]:
    """A single Stat tile with a sparkline (graphMode=area). ``text_mode="name"`` shows the
    ``name_label`` value (an owner holder / an IP); ``value_size`` caps the value font (px) for
    compact one-row bands."""
    defaults: dict[str, Any] = {"mappings": mappings or []}
    if unit is not None:
        defaults["unit"] = unit
    target: dict[str, Any] = {
        "refId": "A",
        "expr": expr,
        "instant": True,
        "datasource": _ds(ds_uid),
    }
    if text_mode == "name":
        target["legendFormat"] = f"{{{{{name_label}}}}}"
    options: dict[str, Any] = {
        "graphMode": "area",
        "textMode": text_mode,
        "reduceOptions": {"calcs": ["lastNotNull"]},
    }
    if value_size is not None:
        options["text"] = {"valueSize": value_size}
    return {
        "type": "stat",
        "title": title,
        "datasource": _ds(ds_uid),
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": options,
    }


def badge(
    expr: str,
    ds_uid: str,
    x: int,
    y: int,
    *,
    mappings: list[dict[str, Any]],
    w: int,
    h: int,
) -> dict[str, Any]:
    """A bold background-coloured header chip (a titleless :func:`stat` with the value font capped,
    the sparkline off, and the whole tile background-coloured by its mapping). The per-site
    LIVE/RECOVERY chips of the Native-HA and RDQM cockpits — derived from the data so the badge
    flips on failover, never a static site label."""
    panel = stat(
        "", expr, ds_uid, x, y, mappings=mappings, w=w, h=h, value_size=_COMPACT_VALUE_SIZE
    )
    panel["options"]["colorMode"] = "background"
    panel["options"]["graphMode"] = "none"
    return panel


def target(ref: str, expr: str, legend: str) -> dict[str, Any]:
    """A timeseries target (range query) with a legend."""
    return {"refId": ref, "expr": expr, "legendFormat": legend, "range": True}


def timeseries(
    title: str,
    targets: list[dict[str, Any]],
    ds_uid: str,
    x: int,
    y: int,
    *,
    w: int = 8,
    h: int = 7,
    unit: str | None = None,
) -> dict[str, Any]:
    """A timeseries panel from range-query targets."""
    defaults: dict[str, Any] = {}
    if unit is not None:
        defaults["unit"] = unit
    return {
        "type": "timeseries",
        "title": title,
        "datasource": _ds(ds_uid),
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [{**t, "datasource": _ds(ds_uid)} for t in targets],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {},
    }


def _norm(series: str, label: str) -> str:
    """The row-key-normalized per-column query: collapse ``node|member|holder`` → ``n``."""
    return f'max by (n)(label_replace({series},"n","$1","{label}","(.*)"))'


def matrix(
    title: str, columns: list[Column], ds_uid: str, y: int, h: int = 9, x: int = 0, w: int = 24
) -> dict[str, Any]:
    """A node×component Table panel: one normalized query per column, joined on ``n``, with a
    per-column colour-background cell mapping. Rows are data-driven; ``h`` sizes the panel."""
    targets: list[dict[str, Any]] = []
    rename: dict[str, str] = {"n": "node"}
    overrides: list[dict[str, Any]] = []
    for i, (col_title, expr, kind) in enumerate(columns):
        ref = _REFIDS[i]
        targets.append(
            {
                "refId": ref,
                "expr": expr,
                "format": "table",
                "instant": True,
                "datasource": _ds(ds_uid),
            },
        )
        rename[f"Value #{ref}"] = col_title
        overrides.append(
            {
                "matcher": {"id": "byName", "options": col_title},
                "properties": [
                    {
                        "id": "custom.cellOptions",
                        "value": {"type": "color-background", "mode": "basic"},
                    },
                    {"id": "mappings", "value": _MAPPINGS[kind]},
                    {"id": "color", "value": {"mode": "fixed"}},
                ],
            },
        )
    return {
        "type": "table",
        "title": title,
        "datasource": _ds(ds_uid),
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": targets,
        "transformations": [
            {"id": "joinByField", "options": {"byField": "n", "mode": "outer"}},
            {
                "id": "organize",
                "options": {"renameByName": rename, "excludeByName": {"Time": True}},
            },
            {"id": "sortBy", "options": {"sort": [{"field": "node"}]}},
        ],
        "fieldConfig": {
            "defaults": {"custom": {"align": "center"}},
            "overrides": overrides,
        },
        "options": {"cellHeight": "sm"},
    }


def state_timeline(
    title: str, signals: list[tuple[str, str]], ds_uid: str, y: int
) -> dict[str, Any]:
    """A State-timeline band of named signals over the drill window (the failover story)."""
    targets = [
        {
            "refId": _REFIDS[i],
            "expr": expr,
            "range": True,
            "legendFormat": name,
            "datasource": _ds(ds_uid),
        }
        for i, (name, expr) in enumerate(signals)
    ]
    return {
        "type": "state-timeline",
        "title": title,
        "datasource": _ds(ds_uid),
        "gridPos": {"h": 7, "w": 24, "x": 0, "y": y},
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "custom": {"fillOpacity": 80},
                "color": {"mode": "thresholds"},
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}],
                },
                "mappings": [
                    {"type": "value", "options": {"0": {"text": "down"}, "1": {"text": "up"}}},
                ],
            },
            "overrides": [],
        },
        "options": {"mergeValues": True, "showValue": "auto"},
    }


def logs_panel(
    title: str, selector: str, loki_uid: str, y: int, *, description: str | None = None
) -> dict[str, Any]:
    """An embedded live log row from Loki. The selector (hosts + units) is board-specific; the
    severity is the shared ``$level`` toggle (see :func:`log_level_var`). An optional description
    surfaces as the panel's info tooltip."""
    ds = {"type": "loki", "uid": loki_uid}
    panel: dict[str, Any] = {
        "type": "logs",
        "title": title,
        "datasource": ds,
        "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
        "targets": [{"refId": "A", "expr": selector, "datasource": ds}],
        "options": {
            "showTime": True,
            "sortOrder": "Descending",
            "enableLogDetails": True,
            "wrapLogMessage": False,
        },
    }
    if description is not None:
        panel["description"] = description
    return panel


def log_level_var() -> dict[str, Any]:
    """The log row's severity toggle: WARN+ (default) or All (incl. info). The selected value is
    the line-filter regex the log query interpolates as ``$level``."""
    warn = {"text": "WARN+", "value": _WARN_REGEX, "selected": True}
    show_all = {"text": "All (incl. info)", "value": ".", "selected": False}
    return {
        "name": "level",
        "type": "custom",
        "label": "Log severity",
        "multi": False,
        "includeAll": False,
        "query": f"WARN+ : {_WARN_REGEX}, All (incl. info) : .",
        "options": [warn, show_all],
        "current": warn,
    }
