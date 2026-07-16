"""The Native HA cluster cockpit — MQ's raft-based Native HA arm, plus its cross-region
replication (CRR): cluster health, the per-site instance matrix (with LIVE/RECOVERY chips), the
CRR card, the failover-and-CRR timeline, and the live log row.

Rides the ``cluster_nha_*`` NATIVEHA contract slice (``cluster_nha_quorum`` / ``cluster_nha_role``
/ ``cluster_nha_role_code`` / ``cluster_nha_insync`` / ``cluster_nha_hastatus`` /
``cluster_nha_hastatus_ok`` / the ``cluster_nha_group_*`` + ``cluster_nha_connected`` CRR family)
together with the shared ``cluster_quorate`` / ``cluster_node_online`` / ``cluster_resource_owner``
families — every metric a panel queries is one ``mqro.collectors.nativeha`` emits, enforced by
``tests.test_contract``.

De-hardcoded from the lab's ``clusterboard`` Native-HA assembly: the QM name, the Ansible group
selector, the per-site member regexes, the log host patterns, and the datasources all come from the
:class:`~mqro.dashboards.profile.DashboardProfile`. Native HA has no DRBD/SAN storage tier, so there
is no storage section; the lab board's node-fabric ``perf`` / ``network`` sections are node-exporter
views outside the ``cluster_*`` contract this bundle owns and are intentionally not ported (as in
:mod:`~mqro.dashboards.boards.cluster`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mqro.dashboards.boards.primitives import (
    _BLUE,
    _COMPACT_VALUE_SIZE,
    _GREEN,
    _RED,
    _STALE_MAP,
    _YELLOW,
    INTEGRITY_MAPS,
    Column,
    _norm,
    badge,
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

# Native HA reports the CRR standby group under MQ's fixed GRPROLE name; the live/recovery split is
# a product constant (like DRBD's "Primary" / "StandAlone"), not a lab identity, so it stays a
# literal — the identities (QM, groups, members, hosts) are what thread through the profile.
_RECOVERY = 'group="Recovery"'


def _sel(profile: DashboardProfile) -> str:
    """The ``{groups=~"a|b"}`` matcher scoping a shared-family query to this cluster."""
    return f'{{groups=~"{profile.groups_selector}"}}'


def _integrity_expr(profile: DashboardProfile) -> str:
    """Native HA cannot split-brain (raft quorum). The hazard reframes around availability +
    durability: quorum-lost ∨ no-Active-owner ∨ a replica out of sync — gated on role data present
    so no-data reads STALE (never a false green)."""
    qm, sel = profile.qm_resource, _sel(profile)
    hazards = (
        f"(min(cluster_quorate{sel}) == bool 0)"
        f' + (absent(cluster_resource_owner{{resource="{qm}"}}) or vector(0))'
        " + (count(cluster_nha_insync == 0) or vector(0))"
    )
    return f"({hazards}) and on() (count(cluster_nha_role) > 0)"


def _status_band(profile: DashboardProfile, y: int) -> list[dict[str, Any]]:
    """① One compact full-width row of five equal tiles — Active instance · Quorum · Instances
    in-sync · HA status · Integrity. No-data reads STALE/DOWN, never healthy (fail-loud)."""
    ds_uid, qm = profile.datasource_uid, profile.qm_resource
    normal = 'max by (member)(cluster_nha_hastatus{status="Normal"})'
    health_maps = [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "DOWN", "index": 0},
                "1": {"color": _GREEN, "text": "✓ Normal", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    vs, h = _COMPACT_VALUE_SIZE, 3
    return [
        # max by (holder) collapses the per-reporter series → one tile (the Active instance)
        stat(
            "Active instance",
            f'max by (holder)(cluster_resource_owner{{resource="{qm}"}})',
            ds_uid,
            0,
            y,
            text_mode="name",
            w=5,
            h=h,
            value_size=vs,
        ),
        stat("Quorum", "max(cluster_nha_quorum)", ds_uid, 5, y, w=4, h=h, value_size=vs),
        stat(
            "Instances in-sync",
            "sum(max by (member)(cluster_nha_insync))",
            ds_uid,
            9,
            y,
            w=5,
            h=h,
            value_size=vs,
        ),
        stat(
            "HA status",
            f"min({normal})",
            ds_uid,
            14,
            y,
            mappings=health_maps,
            w=5,
            h=h,
            value_size=vs,
        ),
        stat(
            "Integrity",
            _integrity_expr(profile),
            ds_uid,
            19,
            y,
            mappings=INTEGRITY_MAPS,
            w=5,
            h=h,
            value_size=vs,
        ),
    ]


def _instance_cols(site_members: str) -> list[Column]:
    """Native-HA instance-matrix columns for one site (member regex selects that site's
    instances): online · role (coded → Active/Leader/Replica/Unknown) · in-sync · HA Normal. No
    corosync/pacemaker/iSCSI/DRBD/fence — Native HA has none."""
    member = f'member=~"{site_members}"'
    return [
        ("online", _norm(f"cluster_node_online{{{member}}}", "member"), "up"),
        ("role", _norm(f"cluster_nha_role_code{{{member}}}", "member"), "role"),
        ("in-sync", _norm(f"cluster_nha_insync{{{member}}}", "member"), "up"),
        ("HA Normal", _norm(f"cluster_nha_hastatus_ok{{{member}}}", "member"), "up"),
    ]


def _site_badge(site_members: str, ds_uid: str, x: int, y: int) -> dict[str, Any]:
    """A bold per-site header chip: LIVE (green) when the site holds the Active instance, RECOVERY
    (blue) when it holds the standby Leader — a healthy standby, not a warning. Derived from the
    data so it flips on failover, never a static site label."""
    maps = [
        {
            "type": "value",
            "options": {
                "2": {"color": _GREEN, "text": "LIVE", "index": 0},
                "3": {"color": _BLUE, "text": "RECOVERY", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    expr = f'max(cluster_nha_role_code{{member=~"{site_members}"}})'
    return badge(expr, ds_uid, x, y, mappings=maps, w=5, h=7)


def _crr_card(profile: DashboardProfile, y: int) -> list[dict[str, Any]]:
    """③ Cross-region (CRR) replication health from the ``dspmq -g`` group view: is the recovery
    group connected, in-sync, and how far behind (backlog). Which site is live/recovery is shown by
    the instance section's badges + role column, so it is not repeated here."""
    ds_uid = profile.datasource_uid
    conn_maps = [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "disconnected", "index": 0},
                "1": {"color": _GREEN, "text": "✓ connected", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    insync_maps = [
        {
            "type": "value",
            "options": {
                "0": {"color": _YELLOW, "text": "catching up", "index": 0},
                "1": {"color": _GREEN, "text": "✓ in-sync", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    vs, h = _COMPACT_VALUE_SIZE, 3
    return [
        stat(
            "CRR connected",
            f"max(cluster_nha_connected{{{_RECOVERY}}})",
            ds_uid,
            0,
            y,
            mappings=conn_maps,
            w=8,
            h=h,
            value_size=vs,
        ),
        stat(
            "CRR in-sync",
            f"max(cluster_nha_group_insync{{{_RECOVERY}}})",
            ds_uid,
            8,
            y,
            mappings=insync_maps,
            w=8,
            h=h,
            value_size=vs,
        ),
        stat(
            "CRR backlog",
            f"max(cluster_nha_group_backlog{{{_RECOVERY}}})",
            ds_uid,
            16,
            y,
            w=8,
            h=h,
            value_size=vs,
        ),
    ]


def _timeline(profile: DashboardProfile, y: int) -> dict[str, Any]:
    """The Native HA failover + CRR story: Active count, quorum, in-sync, CRR-connected. Each count
    collapses per-member first (every node reports every member) so it is the real instance count,
    not multiplied by the number of reporters."""
    signals = [
        ("Active instances", 'count(max by (member)(cluster_nha_role{role="Active"}))'),
        ("quorum", "max(cluster_nha_quorum)"),
        ("instances in-sync", "sum(max by (member)(cluster_nha_insync))"),
        ("CRR connected", f"max(cluster_nha_connected{{{_RECOVERY}}})"),
    ]
    return state_timeline("⟳ Failover & CRR timeline", signals, profile.datasource_uid, y)


def _log_row(profile: DashboardProfile, y: int) -> dict[str, Any]:
    """Native HA logs: MQ-related journald units on this arm's hosts, severity-filtered by the
    shared ``$level`` toggle. The mq-events instrumentation stream is filtered out (the ``mq-.*``
    wildcard would sweep it in)."""
    sel = (
        f'{{host=~"{profile.host_selector}", unit=~".*mqmonitor.*|.*amq.*|.*ibmmq.*|mq-.*", '
        'unit!="mq-events"} |~ `${level}`'
    )
    note = (
        "Shows MQ-related journald units on the Native HA nodes. MQ's own error log "
        f"(/var/mqm/qmgrs/{profile.qm_resource}/errors/AMQERR*.LOG) is file-based, not journald, "
        "so it is not shipped to Loki yet — wire Alloy to tail those files for full QM HA/CRR logs."
    )
    return logs_panel(
        "▤ Native HA logs (severity: $level)", sel, profile.logs_uid, y, description=note
    )


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


def render_nativeha_board(profile: DashboardProfile) -> dict[str, Any]:
    """Assemble the Native HA cluster cockpit for one profile (pure — no I/O). Every identity (QM
    name, group selector, per-site members, log hosts, datasources, title) comes from the
    profile."""
    ds_uid = profile.datasource_uid
    a_members, b_members = profile.site_a_member_selector, profile.site_b_member_selector
    panels = [
        banner(f"## Native HA Cluster · {profile.board_title}", y=0),
        row_header("① Cluster status — active · quorum · in-sync · integrity", y=2),
        *_status_band(profile, y=3),
        row_header("② Instances — Site A & Site B", y=6),
        _site_badge(a_members, ds_uid, 0, 7),
        matrix("Site A", _instance_cols(a_members), ds_uid, y=7, h=7, x=5, w=19),
        _site_badge(b_members, ds_uid, 0, 14),
        matrix("Site B", _instance_cols(b_members), ds_uid, y=14, h=7, x=5, w=19),
        row_header("③ Cross-region replication (CRR)", y=21),
        *_crr_card(profile, y=22),
        row_header("⟳ Failover & CRR timeline", y=25),
        _timeline(profile, y=26),
        row_header("▤ Native HA logs", y=33),
        _log_row(profile, y=34),
    ]
    return {
        "uid": profile.nativeha_board_uid,
        "title": f"Native HA Cluster · {profile.board_title}",
        "schemaVersion": 39,
        "version": 0,
        "panels": panels,
        "templating": {"list": [log_level_var()]},
        "annotations": _annotations(profile),
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "tags": ["mqro", "cockpit", profile.slug],
    }
