"""The messaging-layer board — one board per stack of the MQ flow plus the live application
round-trip. Where the cluster cockpit shows the infrastructure *under* MQ, this shows whether
messaging is actually *flowing*: a status band, the app<->svc message-flow strip, the round-trip
timeline, and the queues/channels tables.

Mostly stock ``ibmmq_*``; the round-trip signal is the external ``app_roundtrip_*`` app-SLA metric
(emitted by the operator's own app probe, not this repo's collectors — documented-as-external, like
``ibmmq_*``). Ported from the lab's ``messagingboard``; every QM/queue/channel name and the uid
derive from the :class:`~mqro.dashboards.profile.DashboardProfile` — no QM literal survives.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from mqro.dashboards.boards.primitives import (
    _STATUS_MAP,
    _ds,
    _qm_status_expr,
    banner,
    log_level_var,
    logs_panel,
    row_header,
    stat,
    target,
    timeseries,
)

if TYPE_CHECKING:
    from mqro.dashboards.profile import DashboardProfile

# Fixed MQSC object names in the app<->svc flow (the QM names are per-profile).
_APP_SVRCONN = "APP.SVRCONN"
_SVC_SVRCONN = "SVC.SVRCONN"
_APP_REPLY = "APP.REPLY"

# Exporter metadata columns stripped from the queue/channel tables (name + value kept).
_TABLE_DROP_COLS = [
    "Time",
    "qmgr",
    "job",
    "instance",
    "__name__",
    "cluster",
    "description",
    "platform",
    "usage",
    "host",
    "type",
]

# The external app round-trip signal (#429): emitted by the app client's node_exporter, one stream
# (the active requester), not qmgr-keyed. Documented-as-external, like the stock ibmmq_* series.
_RT_TOTAL = "app_roundtrip_total"
_RT_FAIL = "app_roundtrip_failures_total"
_RT_BUCKET = "app_roundtrip_latency_ms_bucket"


def _channel_status_expr(qm: str, channel: str) -> str:
    return f'max(ibmmq_channel_status_squash{{qmgr="{qm}",channel="{channel}"}}) or vector(-1)'


def _queue_depth_expr(qm: str, queue: str) -> str:
    return f'max(ibmmq_queue_depth{{qmgr="{qm}",queue="{queue}"}}) or vector(-1)'


def _status_band(
    ds_uid: str, app_qm: str, svc_qm: str, drill_uid: str, y: int
) -> list[dict[str, Any]]:
    """① Flow indicators (not depths): App/SVC QM up · round-trip success % · message rate ·
    failure rate. Success% reads No data when idle (no fake 100% on no traffic). The App QM tile
    carries a data-link into that profile's per-QM board."""
    success = f"100 * (1 - (sum(rate({_RT_FAIL}[5m])) / sum(rate({_RT_TOTAL}[5m]))))"
    specs: list[tuple[str, str, int, int, list[dict[str, Any]] | None, str | None]] = [
        ("App QM", _qm_status_expr(app_qm), 0, 5, _STATUS_MAP, None),
        ("SVC QM", _qm_status_expr(svc_qm), 5, 5, _STATUS_MAP, None),
        ("Round-trip OK %", success, 10, 5, None, "percent"),
        ("Message rate", f"sum(rate({_RT_TOTAL}[1m]))", 15, 5, None, "reqps"),
        ("Failure rate", f"sum(rate({_RT_FAIL}[1m]))", 20, 4, None, "reqps"),
    ]
    tiles = [
        stat(t, e, ds_uid, x, y, mappings=m, unit=u, w=w, h=3, value_size=22)
        for t, e, x, w, m, u in specs
    ]
    tiles[0]["fieldConfig"]["defaults"]["links"] = [
        {"title": "QM state ↗", "url": f"/d/{drill_uid}"}
    ]
    return tiles


def _flow_strip(
    ds_uid: str, app_qm: str, svc_qm: str, req_queue: str, y: int
) -> list[dict[str, Any]]:
    """② The message path as a row of positioned tiles: app-client → APP.SVRCONN → [APP QM] →
    SDR → [SVC QM] → SVC.SVRCONN → svc-sim, with the APP.REPLY return leg. Each hop colours on
    its own metric. ``req_queue`` is this stack's own request queue on the shared SVCQM."""
    sdr = f"{app_qm}.{svc_qm}"
    w = 3
    tiles = [
        ("app-client", f"sum(rate({_RT_TOTAL}[1m]))", "reqps", None),
        (_APP_SVRCONN, _channel_status_expr(app_qm, _APP_SVRCONN), None, _STATUS_MAP),
        (f"{app_qm} (app)", _qm_status_expr(app_qm), None, _STATUS_MAP),
        (sdr, _channel_status_expr(app_qm, sdr), None, _STATUS_MAP),
        (f"{svc_qm} (svc)", _qm_status_expr(svc_qm), None, _STATUS_MAP),
        (_SVC_SVRCONN, _channel_status_expr(svc_qm, _SVC_SVRCONN), None, _STATUS_MAP),
        ("svc-sim: " + req_queue, _queue_depth_expr(svc_qm, req_queue), None, None),
        (_APP_REPLY, _queue_depth_expr(app_qm, _APP_REPLY), None, None),
    ]
    return [
        stat(title, expr, ds_uid, i * w, y, mappings=mapping, unit=unit, w=w, value_size=18)
        for i, (title, expr, unit, mapping) in enumerate(tiles)
    ]


def _roundtrip_timeline(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """⟳ Throughput + failure rate over time beside the latency distribution (p50/p95)."""
    rate = timeseries(
        "Round-trip rate & failures",
        [
            target("A", f"sum(rate({_RT_TOTAL}[1m]))", "throughput"),
            target("B", f"sum(rate({_RT_FAIL}[1m]))", "failures"),
        ],
        ds_uid,
        0,
        y,
        w=12,
        unit="reqps",
    )
    latency = timeseries(
        "Round-trip latency (p50 · p95)",
        [
            target("A", f"histogram_quantile(0.5, sum by (le)(rate({_RT_BUCKET}[5m])))", "p50"),
            target("B", f"histogram_quantile(0.95, sum by (le)(rate({_RT_BUCKET}[5m])))", "p95"),
        ],
        ds_uid,
        12,
        y,
        w=12,
        unit="ms",
    )
    return [rate, latency]


def _object_table(
    ds_uid: str,
    title: str,
    qm: str,
    series: str,
    label: str,
    value_name: str,
    y: int,
    x: int,
    w: int,
    *,
    mappings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A per-object table (rows = queues or channels of one QM), keyed by ``label``. ``mappings``
    colours the value cell (channel status); queues leave it a plain number."""
    name_col = label.capitalize()
    overrides: list[dict[str, Any]] = []
    if mappings is not None:
        overrides.append(
            {
                "matcher": {"id": "byName", "options": value_name},
                "properties": [
                    {
                        "id": "custom.cellOptions",
                        "value": {"type": "color-background", "mode": "basic"},
                    },
                    {"id": "mappings", "value": mappings},
                    {"id": "color", "value": {"mode": "fixed"}},
                ],
            }
        )
    return {
        "type": "table",
        "title": title,
        "datasource": _ds(ds_uid),
        "gridPos": {"h": 8, "w": w, "x": x, "y": y},
        "targets": [
            {
                "refId": "A",
                "expr": f'{series}{{qmgr="{qm}"}}',
                "format": "table",
                "instant": True,
                "datasource": _ds(ds_uid),
            }
        ],
        "transformations": [
            {
                "id": "organize",
                "options": {
                    "excludeByName": dict.fromkeys(_TABLE_DROP_COLS, True),
                    "renameByName": {label: name_col, "Value": value_name},
                },
            },
            {"id": "sortBy", "options": {"sort": [{"field": name_col}]}},
        ],
        "fieldConfig": {"defaults": {"custom": {"align": "center"}}, "overrides": overrides},
        "options": {"cellHeight": "sm"},
    }


def _events_panel(loki_uid: str, objects: list[str], y: int) -> dict[str, Any]:
    """▤ MQ instrumentation events for this stack: the mq-events Loki stream scoped to this
    stack's QMs/queues/channels (``eventSource_objectName`` after ``| json``)."""
    names = "|".join(re.escape(o) for o in objects)
    sel = f'{{unit="mq-events"}} | json | eventSource_objectName=~`{names}`'
    return logs_panel("▤ MQ instrumentation events (this stack)", sel, loki_uid, y)


def _title_banner(slug: str, app_qm: str, svc_qm: str, y: int) -> dict[str, Any]:
    content = f"## Messaging Layer · {slug} · app-client ⇄ {app_qm} ⇄ {svc_qm} ⇄ svc-sim"
    return banner(content, y)


def render_messaging_board(profile: DashboardProfile) -> dict[str, Any]:
    """Assemble the flow-oriented messaging board for one profile (pure — no I/O). The App QM
    status tile drills into that profile's per-QM board (``profile.qm_board_uid``)."""
    ds_uid, loki_uid = profile.datasource_uid, profile.logs_uid
    app_qm, svc_qm, req_queue = profile.app_qm, profile.svc_qm, profile.req_queue
    panels = [
        _title_banner(profile.slug, app_qm, svc_qm, y=0),
        row_header("① Messaging status — QMs · success · rate · failures", y=2),
        *_status_band(ds_uid, app_qm, svc_qm, profile.qm_board_uid, y=3),
        row_header("② The message flow — app-client ⇄ SVC", y=6),
        *_flow_strip(ds_uid, app_qm, svc_qm, req_queue, y=7),
        row_header("⟳ Round-trip — throughput · failures · latency", y=11),
        *_roundtrip_timeline(ds_uid, y=12),
        row_header("▤ Round-trip logs", y=19),
        logs_panel(
            "▤ Round-trip logs (app + svc, severity: $level)",
            '{unit=~"mq-app-requester.*|mq-svc-responder.*"} |~ `${level}`',
            loki_uid,
            y=20,
        ),
        row_header("▤ MQ instrumentation events — this stack", y=28),
        _events_panel(
            loki_uid,
            [
                app_qm,
                svc_qm,
                req_queue,
                _APP_REPLY,
                _APP_SVRCONN,
                _SVC_SVRCONN,
                f"{app_qm}.{svc_qm}",
            ],
            y=29,
        ),
        row_header("▦ Queues & channels", y=37),
        _object_table(
            ds_uid,
            f"{app_qm} — queues",
            app_qm,
            "ibmmq_queue_depth",
            "queue",
            "Depth",
            y=38,
            x=0,
            w=12,
        ),
        _object_table(
            ds_uid,
            f"{app_qm} — channels",
            app_qm,
            "ibmmq_channel_status_squash",
            "channel",
            "Status",
            y=38,
            x=12,
            w=12,
            mappings=_STATUS_MAP,
        ),
    ]
    return {
        "uid": profile.messaging_board_uid,
        "title": f"Messaging Layer · {profile.slug}",
        "schemaVersion": 39,
        "version": 0,
        "panels": panels,
        "templating": {"list": [log_level_var()]},
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "tags": ["mqro", "messaging", profile.slug],
    }
