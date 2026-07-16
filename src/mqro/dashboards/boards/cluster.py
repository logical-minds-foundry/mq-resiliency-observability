"""The Pacemaker/DRBD (SAN) cluster cockpit — the infrastructure view *under* MQ for the
Pacemaker HA arm: cluster health, the node×component compute matrix, DRBD/SAN storage, the
failover timeline, and the live cluster log.

Rides the ``cluster_*`` PACEMAKER contract slice (``cluster_daemon_up`` / ``cluster_iscsi_sessions``
/ ``cluster_fence_count`` / ``cluster_node_online`` / ``cluster_node_unclean`` / ``cluster_quorate``
/ ``cluster_resource_owner`` / ``cluster_resource_started`` / the ``cluster_drbd_*`` family) — every
metric a panel queries is one ``mqro.collectors.cluster`` emits, enforced by
``tests.test_contract``.

De-hardcoded from the lab's ``clusterboard`` PCMK assembly: the Ansible group selector, the
Pacemaker QM resource id, the log host patterns, the datasources, and the title all come from the
:class:`~mqro.dashboards.profile.DashboardProfile`.

The lab board's ``perf`` (node_exporter CPU/disk with lab ``virbr-*`` device names) and ``network``
(``lab_network_*`` fabric) sections are intentionally NOT ported here: they are node-fabric views,
not part of the ``cluster_*`` contract this bundle owns. They are tracked for a follow-up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mqro.dashboards.boards.primitives import (
    _GREEN,
    _RED,
    _STALE_MAP,
    Column,
    _norm,
    banner,
    log_level_var,
    logs_panel,
    matrix,
    row_header,
    stat,
    state_timeline,
)

if TYPE_CHECKING:
    from mqro.dashboards.profile import DashboardProfile

# A shared cluster_* family (cluster_node_online / cluster_quorate) is emitted by EVERY arm's
# collector into one Prometheus, so a board query over it MUST be scoped to its own arm's groups
# or another cluster's nodes leak in. cluster_resource_owner/_started is resource-scoped instead.
_INTEGRITY_MAPS: list[dict[str, Any]] = [
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


def _sel(profile: DashboardProfile) -> str:
    """The ``{groups=~"a|b"}`` matcher scoping a shared-family query to this cluster."""
    return f'{{groups=~"{profile.groups_selector}"}}'


def _hero_tiles(profile: DashboardProfile, y: int) -> list[dict[str, Any]]:
    """The top band: cluster health, nodes online, active QM owner, replication backlog. No-data
    reads STALE, never healthy (fail-loud)."""
    ds_uid = profile.datasource_uid
    online = f"max by (member)(cluster_node_online{_sel(profile)})"
    health_maps = [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "DOWN", "index": 0},
                "1": {"color": _GREEN, "text": "✓ healthy", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    return [
        stat("Cluster health", f"min({online})", ds_uid, 0, y, mappings=health_maps),
        stat("Nodes online", f"sum({online})", ds_uid, 6, y),
        stat(
            "Active QM owner",
            f'max by (holder)(cluster_resource_owner{{resource="{profile.qm_resource}"}})',
            ds_uid,
            12,
            y,
            text_mode="name",
        ),
        stat(
            "Replication backlog",
            "max(cluster_drbd_out_of_sync_bytes)",
            ds_uid,
            18,
            y,
            unit="bytes",
        ),
    ]


def _integrity_panel(profile: DashboardProfile, y: int) -> dict[str, Any]:
    """First-class integrity light: DRBD split-brain / dual-primary / Diskless hazard count,
    gated on DRBD being present so no-data reads STALE (not a false green)."""
    hazards = (
        '(count(cluster_drbd_conn{conn="StandAlone"}) or vector(0))'
        ' + (count(cluster_drbd_disk{disk="Diskless"}) or vector(0))'
        ' + (count(cluster_drbd_role{role="Primary"}) > bool 1)'
    )
    expr = f"({hazards}) and on() (count(cluster_drbd_role) > 0)"
    panel = stat("Integrity", expr, profile.datasource_uid, 0, y, mappings=_INTEGRITY_MAPS)
    panel["gridPos"]["w"] = 24
    return panel


def _compute_cols(profile: DashboardProfile) -> list[Column]:
    """② node×component compute matrix columns: daemons up · iSCSI sessions · fence baseline ·
    online (group-scoped, the shared family) · unclean."""
    sel = _sel(profile)
    return [
        ("corosync", _norm('cluster_daemon_up{unit="corosync"}', "node"), "up"),
        ("pacemaker", _norm('cluster_daemon_up{unit="pacemaker"}', "node"), "up"),
        ("iSCSI", _norm("cluster_iscsi_sessions", "node"), "sessions"),
        ("fence", _norm("cluster_fence_count", "member"), "clean0"),
        ("online", _norm(f"cluster_node_online{sel}", "member"), "up"),
        ("unclean", _norm("cluster_node_unclean", "member"), "clean0"),
    ]


_STORAGE_COLS: list[Column] = [
    ("resync %", _norm("cluster_drbd_resync_pct", "node"), "sessions"),
    ("out-of-sync", _norm("cluster_drbd_out_of_sync_bytes", "node"), "clean0"),
]


def _timeline(profile: DashboardProfile, y: int) -> dict[str, Any]:
    """The failover-story band: nodes online, QM running, DRBD primary, quorate over the drill
    window."""
    sel = _sel(profile)
    signals = [
        ("nodes online", f"sum(max by (member)(cluster_node_online{sel}))"),
        ("QM running", f'max(cluster_resource_started{{resource="{profile.qm_resource}"}})'),
        ("DRBD primary", 'count(cluster_drbd_role{role="Primary"})'),
        ("quorate", f"min(cluster_quorate{sel})"),
    ]
    return state_timeline("⟳ Failover timeline", signals, profile.datasource_uid, y)


def _log_row(profile: DashboardProfile, y: int) -> dict[str, Any]:
    """The cluster-node log row (corosync/pacemaker/drbd/mq units on this cluster's hosts). The
    mq-events instrumentation stream is filtered out (the ``.*mq.*`` wildcard would sweep it in)."""
    sel = (
        f'{{host=~"{profile.host_selector}", unit=~"corosync.*|pacemaker.*|drbd.*|.*mq.*", '
        'unit!="mq-events"} |~ `${level}`'
    )
    return logs_panel("▤ Cluster logs (severity: $level)", sel, profile.logs_uid, y)


def _annotations(profile: DashboardProfile) -> dict[str, Any]:
    """The always-on QM-failover marker. cluster_resource_owner carries the holder in a label, so
    an owner change spawns a NEW series — count owners per resource and watch THAT change."""
    owner_change = "changes((count by (resource)(cluster_resource_owner))[5m:])"
    return {
        "list": [
            {
                "name": "QM failover (owner change)",
                "datasource": {"type": "prometheus", "uid": profile.datasource_uid},
                "enable": True,
                "hide": True,
                "iconColor": "orange",
                "expr": owner_change,
                "step": "10s",
            },
        ],
    }


def render_cluster_board(profile: DashboardProfile) -> dict[str, Any]:
    """Assemble the Pacemaker/DRBD cluster cockpit for one profile (pure — no I/O). Every
    identity (group selector, QM resource id, log hosts, datasources, title) comes from the
    profile."""
    ds_uid = profile.datasource_uid
    panels = [
        banner(f"## PCMK Cluster · {profile.board_title}", y=0),
        row_header("① Cluster status — health · owner · quorum · integrity", y=2),
        *_hero_tiles(profile, y=3),
        _integrity_panel(profile, y=7),
        matrix("② Compute — node × component", _compute_cols(profile), ds_uid, y=10),
        matrix("③ Storage — DRBD / SAN", _STORAGE_COLS, ds_uid, y=19, h=5),
        row_header("⟳ Failover timeline", y=24),
        _timeline(profile, y=25),
        row_header("▤ Cluster logs", y=32),
        _log_row(profile, y=33),
    ]
    return {
        "uid": profile.cluster_board_uid,
        "title": f"PCMK Cluster · {profile.board_title}",
        "schemaVersion": 39,
        "version": 0,
        "panels": panels,
        "templating": {"list": [log_level_var()]},
        "annotations": _annotations(profile),
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "tags": ["mqro", "cockpit", profile.slug],
    }
