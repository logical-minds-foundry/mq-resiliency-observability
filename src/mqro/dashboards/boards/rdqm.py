"""The RDQM cluster cockpit — the richest arm: DRBD + Pacemaker HA (rdqmadm) with Native-HA-style
roles AND cross-site DR (rdqmdr) AND a real DRBD storage tier. Cluster health, the per-site
instance matrix (with LIVE/RECOVERY/NOT-READY chips), the Pacemaker resource matrix rdqmadm wraps,
the DRBD storage matrix, the cross-site DR card, the failover timeline, and the live log row.

Rides the ``cluster_rdqm_*`` RDQM contract slice (``cluster_rdqm_ha_status_ok`` /
``cluster_rdqm_role_code`` / ``cluster_rdqm_qm_running`` / ``cluster_rdqm_node_ready`` /
``cluster_rdqm_floating_ip`` / ``cluster_rdqm_pm_state`` / ``cluster_rdqm_failcount`` /
``cluster_rdqm_qm_startable`` / the ``cluster_rdqm_dr_*`` family) together with the shared
``cluster_node_online`` / ``cluster_resource_owner`` and ``cluster_drbd_*`` families — every metric
a panel queries is one ``mqro.collectors.rdqm`` emits, enforced by ``tests.test_contract``.

De-hardcoded from the lab's ``clusterboard`` RDQM assembly: the QM name, the Ansible group
selectors, the per-site member regexes, the DRBD / Pacemaker resource names (all derived from the
one ``drbd_resource`` base), the log host patterns, and the datasources all come from the
:class:`~mqro.dashboards.profile.DashboardProfile`. The lab board's node-fabric ``perf`` /
``network`` sections are node-exporter views outside the ``cluster_*`` contract this bundle owns and
are intentionally not ported (as in :mod:`~mqro.dashboards.boards.cluster`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mqro.dashboards.boards.primitives import (
    _BLUE,
    _COMPACT_VALUE_SIZE,
    _GREEN,
    _RED,
    _STALE_MAP,
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

# px floor per column; low enough that the 5–6 column matrices shrink to fit rather than overflow
# into a horizontal scrollbar.
_MATRIX_MIN_WIDTH = 80


def _sel(profile: DashboardProfile) -> str:
    """The ``{groups=~"a|b"}`` matcher scoping a shared-family query to this cluster."""
    return f'{{groups=~"{profile.groups_selector}"}}'


def _fit_table(panel: dict[str, Any]) -> dict[str, Any]:
    """Make a matrix() table show exactly its real columns AND fit the panel width.

    Two fits: (1) ``joinByField`` can carry a leftover Time field per instant query that organize
    does not fully exclude — append a ``filterFieldsByName`` that keeps ONLY node + the real value
    columns; (2) drop the per-column minWidth so Grafana shrinks the columns to fit instead of
    overflowing into a horizontal scrollbar."""
    org = next(t for t in panel["transformations"] if t["id"] == "organize")
    keep = list(org["options"]["renameByName"].values())  # ["node", <column titles…>]
    panel["transformations"].append(
        {"id": "filterFieldsByName", "options": {"include": {"names": keep}}}
    )
    panel["fieldConfig"]["defaults"].setdefault("custom", {})["minWidth"] = _MATRIX_MIN_WIDTH
    return panel


def _integrity_expr(profile: DashboardProfile) -> str:
    """RDQM is DRBD under Pacemaker, so it CAN split-brain — a Pacemaker-style integrity light. It
    goes loud on the DRBD storage hazards (StandAlone / per-resource-per-site dual-primary /
    Diskless) PLUS the RDQM HA status ≠ Normal PLUS a whole site with no startable node, gated on
    data present so no-data reads STALE. DRBD hazards are group-scoped so another arm's DRBD can't
    trip this light."""
    g = f'groups=~"{profile.groups_selector}"'
    # dual-primary counted per (resource, groups): a DR pair legitimately runs one primary on each
    # side, so a global per-resource count of 2 is normal, not split-brain.
    hazards = (
        f'(count(cluster_drbd_conn{{conn="StandAlone",{g}}}) or vector(0))'
        f' + (count(cluster_drbd_disk{{disk="Diskless",{g}}}) or vector(0))'
        f' + (sum(count by (resource, groups)(cluster_drbd_role{{role="Primary",{g}}}) > bool 1)'
        " or vector(0))"
        " + (count(cluster_rdqm_ha_status_ok == 0) or vector(0))"
        " + (count(max by (groups)(cluster_rdqm_qm_startable) == 0) or vector(0))"
    )
    return f"({hazards}) and on() (count(cluster_rdqm_ha_status_ok) > 0)"


def _status_band(profile: DashboardProfile, y: int) -> list[dict[str, Any]]:
    """① One compact full-width row of five equal tiles — Running-on node · HA status · Nodes
    online · Floating IP (the single VIP, first-class) · Integrity."""
    ds_uid, qm = profile.datasource_uid, profile.qm_resource
    online = f"max by (member)(cluster_node_online{_sel(profile)})"
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
        stat(
            "Running on",
            f'max by (holder)(cluster_resource_owner{{resource="{qm}"}})',
            ds_uid,
            0,
            y,
            text_mode="name",
            w=5,
            h=h,
            value_size=vs,
        ),
        stat(
            "HA status",
            "min(cluster_rdqm_ha_status_ok)",
            ds_uid,
            5,
            y,
            mappings=health_maps,
            w=4,
            h=h,
            value_size=vs,
        ),
        stat("Nodes online", f"sum({online})", ds_uid, 9, y, w=5, h=h, value_size=vs),
        stat(
            "Floating IP",
            "max by (ip)(cluster_rdqm_floating_ip)",
            ds_uid,
            14,
            y,
            text_mode="name",
            name_label="ip",
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


def _instance_cols(profile: DashboardProfile, site_members: str) -> list[Column]:
    """② RDQM instance-matrix columns for one site: HA status (online if Normal) · role
    (Primary/Secondary, coded) · QM-running · a single Pacemaker summary cell (node_ready = online
    AND startable) · DRBD in-sync. The DRBD column pins the HA resource so the cross-site DR
    resource never bleeds into the in-sync cell."""
    member = f'member=~"{site_members}"'
    node = f'node=~"{site_members}"'
    res = profile.drbd_resource
    return [
        ("HA status", _norm(f"cluster_node_online{{{member}}}", "member"), "up"),
        ("role", _norm(f"cluster_rdqm_role_code{{{member}}}", "member"), "rdqm_role"),
        ("QM running", _norm(f"cluster_rdqm_qm_running{{{member}}}", "member"), "qm_running"),
        ("Pacemaker", _norm(f"cluster_rdqm_node_ready{{{member}}}", "member"), "node_ready"),
        (
            "DRBD in-sync",
            _norm(f'cluster_drbd_disk{{resource="{res}",disk="UpToDate",{node}}}', "node"),
            "up",
        ),
    ]


def _site_badge(group: str, ds_uid: str, x: int, y: int) -> dict[str, Any]:
    """A bold per-site header chip from the site's DR role AND Pacemaker startability, so it flips
    on an rdqmdr cutover and goes loud when the side can't fail over: LIVE (green) when the site is
    the DR primary; RECOVERY (blue) when it is the DR standby with a startable node; NOT READY
    (red) when a recovery site is banned. The startable term is ``or vector(0)``-guarded so the
    LIVE side and a not-yet-instrumented recovery side never read a false red."""
    sel = f'{{groups=~"{group}"}}'
    role = f"max(cluster_rdqm_dr_role_code{sel})"
    banned = f"(max(cluster_rdqm_qm_startable{sel}) == bool 0 or vector(0))"
    expr = f"(2 * ({role} == bool 2)) + (({role} == bool 3) * (1 - {banned}))"
    maps = [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "⚠ NOT READY", "index": 0},
                "1": {"color": _BLUE, "text": "RECOVERY", "index": 1},
                "2": {"color": _GREEN, "text": "LIVE", "index": 2},
            },
        },
        _STALE_MAP,
    ]
    return badge(expr, ds_uid, x, y, mappings=maps, w=4, h=6)


def _pacemaker_cols(profile: DashboardProfile, site_members: str) -> list[Column]:
    """③ The Pacemaker resource layer rdqmadm wraps, exposed like the PCMK board's compute matrix:
    per node — ready · the QM resource · the HA + DR DRBD clones · the floating-IP resource (each a
    pm_state cell) · the QM fail-count. The fail-count cell turns RED when Pacemaker has banned the
    QM from a node — the failure rdqmstatus reports as HA-Normal and the board was blind to."""
    member = f'member=~"{site_members}"'

    def state(resource: str) -> str:
        return _norm(f'cluster_rdqm_pm_state{{resource="{resource}",{member}}}', "member")

    return [
        ("ready", _norm(f"cluster_rdqm_node_ready{{{member}}}", "member"), "node_ready"),
        ("QM", state(profile.drbd_resource), "pm_state"),
        ("DRBD HA", state(profile.pm_drbd_ha_resource), "pm_state"),
        ("DR repl", state(profile.pm_drbd_dr_resource), "pm_state"),
        ("float-IP", state(profile.pm_ip_resource), "pm_state"),
        ("fail-count", _norm(f"cluster_rdqm_failcount{{{member}}}", "member"), "failcount"),
    ]


def _storage_cols(profile: DashboardProfile) -> list[Column]:
    """④ Storage — DRBD: per-node resync % · out-of-sync for the HA resource, across every node.
    The cross-site DR resource is surfaced as the ⑤ DR backlog, not here, so this matrix is the
    intra-site HA replication health."""
    res = f'resource="{profile.drbd_resource}",node=~"{profile.all_members_selector}"'
    return [
        ("resync %", _norm(f"cluster_drbd_resync_pct{{{res}}}", "node"), "sessions"),
        # tolerant mapping: a benign sub-extent secondary↔secondary delta reads green
        ("out-of-sync", _norm(f"cluster_drbd_out_of_sync_bytes{{{res}}}", "node"), "drbd_oos"),
    ]


def _dr_card(profile: DashboardProfile, y: int) -> list[dict[str, Any]]:
    """⑤ Cross-site DR (rdqmdr): DR status · which site is DR primary · failover-ready · replication
    backlog. rdqmstatus reports no backlog field, so the honest backlog is the DR DRBD resource's
    out-of-sync bytes."""
    ds_uid = profile.datasource_uid
    status_maps = [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "DEGRADED", "index": 0},
                "1": {"color": _GREEN, "text": "✓ Normal", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    ready_maps = [
        {"type": "value", "options": {"0": {"color": _GREEN, "text": "✓ Ready", "index": 0}}},
        {
            "type": "range",
            "options": {
                "from": 1,
                "to": 9999,
                "result": {"color": _RED, "text": "⚠ NOT READY", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    # a banned recovery side (0) gated on data present: no startable data → STALE, not false Ready.
    failover_ready = (
        "(count(max by (groups)(cluster_rdqm_qm_startable) == 0) or vector(0))"
        " and on() (count(cluster_rdqm_qm_startable) > 0)"
    )
    # name the SIDE that is DR-primary (not its hosts): collapse the side's nodes with
    # `max by (groups)`, then relabel the group to a friendly site name.
    dr_primary = (
        "label_replace(label_replace("
        'max by (groups)(cluster_rdqm_dr_role{role="Primary"}),'
        f'"site","Site A","groups","{profile.site_a_group}"),'
        f'"site","Site B","groups","{profile.site_b_group}")'
    )
    vs, h = _COMPACT_VALUE_SIZE, 3
    return [
        stat(
            "DR status",
            "min(cluster_rdqm_dr_status_ok)",
            ds_uid,
            0,
            y,
            mappings=status_maps,
            w=6,
            h=h,
            value_size=vs,
        ),
        stat(
            "DR primary",
            dr_primary,
            ds_uid,
            6,
            y,
            text_mode="name",
            name_label="site",
            w=6,
            h=h,
            value_size=vs,
        ),
        stat(
            "Failover ready",
            failover_ready,
            ds_uid,
            12,
            y,
            mappings=ready_maps,
            w=6,
            h=h,
            value_size=vs,
        ),
        stat(
            "DR backlog",
            f'max(cluster_drbd_out_of_sync_bytes{{resource="{profile.drbd_dr_resource}"}})',
            ds_uid,
            18,
            y,
            unit="bytes",
            w=6,
            h=h,
            value_size=vs,
        ),
    ]


def _timeline(profile: DashboardProfile, y: int) -> dict[str, Any]:
    """The RDQM failover story: QM running, HA Normal, DRBD primary present, DR connected."""
    res = profile.drbd_resource
    signals = [
        ("QM running", "max(cluster_rdqm_qm_running)"),
        ("HA Normal", "min(cluster_rdqm_ha_status_ok)"),
        ("DRBD primary", f'count(cluster_drbd_role{{resource="{res}",role="Primary"}})'),
        ("DR connected", "min(cluster_rdqm_dr_status_ok)"),
    ]
    return state_timeline("⟳ Failover timeline", signals, profile.datasource_uid, y)


def _log_row(profile: DashboardProfile, y: int) -> dict[str, Any]:
    """RDQM logs: the Pacemaker/DRBD/MQ journald units on this arm's hosts, severity-filtered by
    the shared ``$level`` toggle. The mq-events instrumentation stream is filtered out (the
    ``mq-.*`` wildcard would sweep it in)."""
    sel = (
        f'{{host=~"{profile.host_selector}", unit=~"pacemaker.*|corosync.*|drbd.*|.*mqmonitor.*'
        '|.*amq.*|.*ibmmq.*|mq-.*", unit!="mq-events"} |~ `${level}`'
    )
    note = (
        "Shows Pacemaker/DRBD/MQ journald units on the RDQM nodes. MQ's own error log "
        f"(/var/mqm/qmgrs/{profile.qm_resource}/errors/AMQERR*.LOG) is file-based, not journald, "
        "so it is not shipped to Loki yet — wire Alloy to tail those files for full QM HA/DR logs."
    )
    return logs_panel("▤ RDQM logs (severity: $level)", sel, profile.logs_uid, y, description=note)


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


def render_rdqm_board(profile: DashboardProfile) -> dict[str, Any]:
    """Assemble the RDQM cluster cockpit for one profile (pure — no I/O). Every identity (QM name,
    group selectors, per-site members, DRBD/Pacemaker resource names, log hosts, datasources, title)
    comes from the profile."""
    ds_uid = profile.datasource_uid
    a_members, b_members = profile.site_a_member_selector, profile.site_b_member_selector
    a_group, b_group = profile.site_a_group, profile.site_b_group
    panels = [
        banner(f"## RDQM Cluster · {profile.board_title}", y=0),
        row_header("① Cluster status — running-on · HA · floating IP · integrity", y=2),
        *_status_band(profile, y=3),
        row_header("② Instances — Site A & Site B", y=6),
        _site_badge(a_group, ds_uid, 0, 7),
        _fit_table(
            matrix("Site A", _instance_cols(profile, a_members), ds_uid, y=7, h=6, x=4, w=20)
        ),
        _site_badge(b_group, ds_uid, 0, 13),
        _fit_table(
            matrix("Site B", _instance_cols(profile, b_members), ds_uid, y=13, h=6, x=4, w=20)
        ),
        row_header("③ Pacemaker resources — Site A & Site B", y=19),
        _fit_table(
            matrix("Pacemaker — Site A", _pacemaker_cols(profile, a_members), ds_uid, y=20, h=6)
        ),
        _fit_table(
            matrix("Pacemaker — Site B", _pacemaker_cols(profile, b_members), ds_uid, y=26, h=6)
        ),
        row_header("④ Storage — DRBD", y=32),
        _fit_table(matrix("Storage — DRBD", _storage_cols(profile), ds_uid, y=33, h=10)),
        row_header("⑤ Cross-site DR (rdqmdr)", y=43),
        *_dr_card(profile, y=44),
        row_header("⟳ Failover timeline", y=47),
        _timeline(profile, y=48),
        row_header("▤ RDQM logs", y=55),
        _log_row(profile, y=56),
    ]
    return {
        "uid": profile.rdqm_board_uid,
        "title": f"RDQM Cluster · {profile.board_title}",
        "schemaVersion": 39,
        "version": 0,
        "panels": panels,
        "templating": {"list": [log_level_var()]},
        "annotations": _annotations(profile),
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "tags": ["mqro", "cockpit", profile.slug],
    }
