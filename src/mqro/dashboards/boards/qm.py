"""The per-QM state board — one board per app queue manager, answering "how is *this* QM doing,
and *how is it trending*?": the QM's own health (a compact stat band + trend graphs), its critical
application queues, and its critical channels.

Stock-only: every series is a stock ``ibmmq_*`` the mq_prometheus exporter already scrapes — no
``cluster_*`` collector dependency, so this board carries no metrics contract of its own. Ported
from the lab's ``qmboard`` with every identity (QM/queue/channel names, the uid, the datasources)
taken from the :class:`~mqro.dashboards.profile.DashboardProfile` — no QM literal survives. Only the
fixed MQSC object names (APP.REPLY, APP.SVRCONN) stay constant, as they do in the lab.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mqro.dashboards.boards.primitives import (
    _STALE_MAP,
    _STATUS_MAP,
    _qm_status_expr,
    banner,
    logs_panel,
    row_header,
    stat,
    target,
    timeseries,
)

if TYPE_CHECKING:
    from mqro.dashboards.profile import DashboardProfile

# Fixed MQSC object names in the app<->svc flow (the QM names are per-profile).
_APP_REPLY = "APP.REPLY"
_APP_SVRCONN = "APP.SVRCONN"

# QM/channel status share the -1/0/1/2 coloured family; every status tile carries STALE so a
# truly-null series never reads healthy.
_QM_STATUS_MAP: list[dict[str, Any]] = [*_STATUS_MAP, _STALE_MAP]

# The services pill: 1 (all up) / 0 (something down) / -1 (no data).
_SERVICES_MAP: list[dict[str, Any]] = [
    {
        "type": "value",
        "options": {
            "-1": {"text": "No data", "color": "grey", "index": 0},
            "0": {"text": "⚠ issue", "color": "red", "index": 1},
            "1": {"text": "✓ up", "color": "green", "index": 2},
        },
    },
    _STALE_MAP,
]


def _uptime_expr(qm: str) -> str:
    return f'max(ibmmq_qmgr_uptime{{qmgr="{qm}"}}) or vector(-1)'


def _connections_expr(qm: str) -> str:
    return f'max(ibmmq_qmgr_connection_count{{qmgr="{qm}"}}) or vector(-1)'


def _msg_rate_expr(qm: str) -> str:
    return (
        f'rate(ibmmq_qmgr_interval_mqput_mqput1_total_count{{qmgr="{qm}"}}[1m]) '
        f'+ rate(ibmmq_qmgr_interval_destructive_get_total_count{{qmgr="{qm}"}}[1m]) '
        "or vector(-1)"
    )


def _services_expr(qm: str) -> str:
    initiator = f'(max(ibmmq_qmgr_channel_initiator_status{{qmgr="{qm}"}}) == bool 2)'
    cmd_server = f'(max(ibmmq_qmgr_command_server_status{{qmgr="{qm}"}}) == bool 2)'
    listener = f'(max(ibmmq_qmgr_active_listeners{{qmgr="{qm}"}}) > bool 0)'
    return f"{initiator} * {cmd_server} * {listener} or vector(-1)"


def _recovery_log_expr(qm: str) -> str:
    restart = f'max(ibmmq_qmgr_log_size_restart{{qmgr="{qm}"}})'
    reusable = f'max(ibmmq_qmgr_log_size_reusable{{qmgr="{qm}"}})'
    return f"100 * {restart} / ({restart} + {reusable}) or vector(-1)"


def _qm_band(ds_uid: str, app_qm: str, y: int) -> list[dict[str, Any]]:
    """QM health: three status pills (Status · Uptime · Services) over three trend graphs
    (Connections · Msg rate · Recovery-log %)."""
    pills = [
        stat(
            "Status",
            _qm_status_expr(app_qm),
            ds_uid,
            0,
            y,
            mappings=_QM_STATUS_MAP,
            w=8,
            h=3,
            value_size=22,
        ),
        stat(
            "Uptime",
            _uptime_expr(app_qm),
            ds_uid,
            8,
            y,
            unit="dtdurations",
            w=8,
            h=3,
            value_size=22,
        ),
        stat(
            "Services",
            _services_expr(app_qm),
            ds_uid,
            16,
            y,
            mappings=_SERVICES_MAP,
            w=8,
            h=3,
            value_size=22,
        ),
    ]
    ty = y + 3
    trends = [
        timeseries(
            "Connections",
            [target("A", _connections_expr(app_qm), "connections")],
            ds_uid,
            0,
            ty,
            w=8,
        ),
        timeseries(
            "Msg rate (put+get / s)",
            [target("A", _msg_rate_expr(app_qm), "put+get / s")],
            ds_uid,
            8,
            ty,
            w=8,
            unit="short",
        ),
        timeseries(
            "Recovery log %",
            [target("A", _recovery_log_expr(app_qm), "log used")],
            ds_uid,
            16,
            ty,
            w=8,
            unit="percent",
        ),
    ]
    return [*pills, *trends]


def _q_series(metric: str, qm: str, obj: str) -> str:
    return f'max(ibmmq_queue_{metric}{{qmgr="{qm}",queue="{obj}"}})'


def _c_series(metric: str, qm: str, obj: str) -> str:
    return f'max(ibmmq_channel_{metric}{{qmgr="{qm}",channel="{obj}"}})'


def _c_rate(metric: str, qm: str, obj: str) -> str:
    return f'sum(rate(ibmmq_channel_{metric}{{qmgr="{qm}",channel="{obj}"}}[1m]))'


def _q_put_rate(qm: str, obj: str) -> str:
    return f'sum(rate(ibmmq_queue_mqput_mqput1_count{{qmgr="{qm}",queue="{obj}"}}[1m]))'


def _q_get_rate(qm: str, obj: str) -> str:
    return f'sum(rate(ibmmq_queue_mqget_count{{qmgr="{qm}",queue="{obj}"}}[1m]))'


def _channel_status_series(qm: str, obj: str) -> str:
    return f"{_c_series('status_squash', qm, obj)} or vector(-1)"


def _queue_block(ds_uid: str, qm: str, queue: str, y: int) -> list[dict[str, Any]]:
    """One queue's block: a labelled row header + four trend graphs (depth · flow · handles · age)
    in a 2x2 grid. Related series share a graph (the relationship is the signal)."""
    header = row_header(f"② Queue · {queue} — depth · flow · handles", y)
    py = y + 1
    panels = [
        timeseries(
            f"{queue} — depth",
            [
                target("A", _q_series("depth", qm, queue), "depth"),
                target("B", _q_series("attribute_max_depth", qm, queue), "max depth"),
            ],
            ds_uid,
            0,
            py,
            w=12,
        ),
        timeseries(
            f"{queue} — flow (put vs get / s)",
            [
                target("A", _q_put_rate(qm, queue), "put rate"),
                target("B", _q_get_rate(qm, queue), "get rate"),
            ],
            ds_uid,
            12,
            py,
            w=12,
            unit="ops",
        ),
        timeseries(
            f"{queue} — handles (in vs out)",
            [
                target("A", _q_series("input_handles", qm, queue), "input"),
                target("B", _q_series("output_handles", qm, queue), "output"),
            ],
            ds_uid,
            0,
            py + 7,
            w=12,
        ),
        timeseries(
            f"{queue} — age & in-flight",
            [
                target("A", _q_series("oldest_message_age", qm, queue), "oldest age"),
                target("B", _q_series("uncommitted_messages", qm, queue), "uncommitted"),
            ],
            ds_uid,
            12,
            py + 7,
            w=12,
        ),
    ]
    return [header, *panels]


def _channel_block(ds_uid: str, qm: str, channel: str, role: str, y: int) -> list[dict[str, Any]]:
    """One channel's block: a labelled row header + three trend graphs (throughput · nettime ·
    status), so a drop is visible in the status timeline."""
    header = row_header(f"③ Channel · {channel} ({role}) — throughput · nettime · status", y)
    py = y + 1
    panels = [
        timeseries(
            f"{channel} — throughput (per-sec)",
            [
                target("A", _c_rate("messages", qm, channel), "messages/s"),
                target("B", _c_rate("bytes_sent", qm, channel), "bytes sent/s"),
                target("C", _c_rate("bytes_rcvd", qm, channel), "bytes rcvd/s"),
            ],
            ds_uid,
            0,
            py,
            w=8,
        ),
        timeseries(
            f"{channel} — nettime",
            [target("A", _c_series("nettime_short", qm, channel), "nettime")],
            ds_uid,
            8,
            py,
            w=8,
            unit="µs",
        ),
        timeseries(
            f"{channel} — status",
            [target("A", _channel_status_series(qm, channel), "status")],
            ds_uid,
            16,
            py,
            w=8,
        ),
    ]
    return [header, *panels]


def _title_banner(slug: str, app_qm: str, y: int) -> dict[str, Any]:
    content = f"## Queue Manager · {slug} · {app_qm} — health · critical queues · channels (trends)"
    return banner(content, y)


def _events_panel(loki_uid: str, title: str, filter_clause: str, y: int) -> dict[str, Any]:
    """A per-object MQ instrumentation-events panel: the mq-events Loki stream, scoped by a
    ``| json`` filter clause so each object's events sit inline with its metric graphs."""
    sel = f'{{unit="mq-events"}} | json | {filter_clause}'
    return logs_panel(title, sel, loki_uid, y)


def render_qm_board(profile: DashboardProfile) -> dict[str, Any]:
    """Assemble one profile's app-QM board (pure — no I/O). QM/queue/channel names and the uid
    all derive from the profile (``<short>APP`` app QM, ``<svc_short>QM`` counterparty)."""
    ds_uid, loki_uid = profile.datasource_uid, profile.logs_uid
    app_qm, svc_qm = profile.app_qm, profile.svc_qm

    panels: list[dict[str, Any]] = [_title_banner(profile.slug, app_qm, y=0)]
    y = 2
    panels.append(row_header("① QM health — status · uptime · services · connections", y))
    y += 1
    panels.extend(_qm_band(ds_uid, app_qm, y))
    y += 10
    panels.append(
        _events_panel(
            loki_uid, f"▤ {app_qm} — all QM events", f'eventData_queueMgrName="{app_qm}"', y
        )
    )
    y += 8

    # Critical queues: APP.REPLY (replies land here) and the svc XMITQ (named after the
    # counterparty QM, USAGE(XMITQ) — the canonical request-outbound path).
    for queue in (_APP_REPLY, svc_qm):
        panels.extend(_queue_block(ds_uid, app_qm, queue, y))
        y += 15
        panels.append(
            _events_panel(loki_uid, f"▤ {queue} — events", f'eventSource_objectName="{queue}"', y)
        )
        y += 8

    # Critical channels: APP.SVRCONN (app client in), <app>.<svc> (SDR), <svc>.<app> (RCVR).
    channels = [
        (_APP_SVRCONN, "SVRCONN"),
        (f"{app_qm}.{svc_qm}", "SDR"),
        (f"{svc_qm}.{app_qm}", "RCVR"),
    ]
    for channel, role in channels:
        panels.extend(_channel_block(ds_uid, app_qm, channel, role, y))
        y += 8
        panels.append(
            _events_panel(
                loki_uid, f"▤ {channel} — events", f'eventSource_objectName="{channel}"', y
            )
        )
        y += 8

    return {
        "uid": profile.qm_board_uid,
        "title": f"Queue Manager · {app_qm}",
        "schemaVersion": 39,
        "version": 0,
        "panels": panels,
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "tags": ["mqro", "qm", profile.slug],
    }
