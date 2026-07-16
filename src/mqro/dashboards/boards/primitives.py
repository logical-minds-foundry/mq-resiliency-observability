"""The Grafana panel toolkit — data in → panel/dashboard dicts out, no I/O.

Ported from the lab's ``clusterboard`` primitives (one toolkit, several board layers). The single
de-hardcoding change: every builder takes its datasource UID as an argument, so no ``"prometheus"``
/ ``"loki"`` literal survives. Everything here is a pure function of its arguments, so a render is
deterministic — the property the dashboard tests assert.

Only the pieces the shipped boards use are ported (the stat/timeseries/table/logs/state-timeline
builders, the ``up`` / ``clean0`` / ``sessions`` colour mappings, the row-key normalizer). The
Native-HA / RDQM colour families and site-badge helpers belong to boards not in this slice and are
deferred with them, so no dead branch dilutes the 100%-branch bar.
"""

from __future__ import annotations

from typing import Any

# (title, promql, mapping_kind) — one normalized column of a node×component matrix.
Column = tuple[str, str, str]

_GREEN, _RED = "green", "red"
_REFIDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Compact value-font (px) for single-row status bands — caps Grafana's auto-fit so the tiles do
# not waste vertical space with huge numbers.
_COMPACT_VALUE_SIZE = 22

# Colour mappings the shipped boards use. 1→green/0→red (up), 0→green/≥1→red (clean0), and
# ≥1→green/0→red (sessions). The role/pm_state/failcount families ride the deferred HA boards.
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
}

# A truly-null series must never read healthy: STALE wins on no-data (fail-loud).
_STALE_MAP: dict[str, Any] = {
    "type": "special",
    "options": {"match": "null", "result": {"color": "text", "text": "STALE", "index": 9}},
}

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
