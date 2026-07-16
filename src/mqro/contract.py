"""The versioned EMITTED-METRICS contract — half 1 of the two-half metrics contract (§3.3).

This module is a structured, introspectable declaration of the ``cluster_*`` / ``cluster_nha_*``
metric families the collectors emit: their names, their label keys, and their value semantics.
Because the collectors and the dashboards live in **one repo**, this half is a *single-repo
consistency invariant* — enforced by :mod:`tests.test_contract`, which asserts every metric named
here is one the collector actually emits (and vice-versa). Drift in either direction fails the
test rather than silently blanking a panel.

Two things this module deliberately is **not**:

- It is **not** the second half of the contract. The REQUIRED SCRAPE-SIDE LABELS the dashboards'
  PromQL assumes but the collectors do *not* emit (the ``groups`` label sourced from Ansible
  inventory, plus host-name patterns) are an operator-side relabeling concern, documented in the
  README — the single biggest adopter trap — not declared here.
- It does **not** pin dynamic label *values*. ``resource`` is the queue-manager name from the
  runtime profile, ``holder`` / ``member`` / ``node`` are host names, ``group`` is a Native HA
  group name. Only the numeric role codes are a fixed value semantic (see ``cluster_nha_role_code``
  in :data:`EMITTED`); everything else is parameterized at deploy time.

Stdlib-only, like every module the collectors ship with.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Schema version of THIS contract, bumped when the emitted-metrics shape changes (a family added,
# a label renamed, a value semantic redefined). Independent of the package's semver in ``VERSION``:
# this tracks the metrics interface dashboards and adopters bind to, not the release.
CONTRACT_VERSION = "1"

# Collector identifiers. One per emitting module; the consistency test iterates per collector so
# each collector's slice of the contract is checked against that collector's own output. Several
# families (cluster_quorate, cluster_node_online, cluster_resource_owner, the last-write timestamp)
# are emitted by MORE THAN ONE collector with identical labels/semantics — a shared family, declared
# once per emitting collector so each collector's slice stays independently checkable.
NATIVEHA = "nativeha"
PACEMAKER = "pacemaker"
RDQM = "rdqm"


class MetricKind(Enum):
    """How a metric's numeric value is meant to be read.

    - ``GAUGE``     — a plain numeric reading (a count).
    - ``BOOLEAN``   — 0 or 1, a healthy/condition flag.
    - ``STATE``     — an info-style series whose value is a constant ``1``; the meaningful state
      travels in a label (e.g. ``role`` / ``status``), so a PromQL join can surface it.
    - ``ENUM``      — a numeric-coded state (a small integer with a fixed meaning per code).
    - ``TIMESTAMP`` — a Unix time; staleness is read as "stopped advancing".
    """

    GAUGE = "gauge"
    BOOLEAN = "boolean"
    STATE = "state"
    ENUM = "enum"
    TIMESTAMP = "timestamp"


@dataclass(frozen=True)
class MetricSpec:
    """One emitted metric family: its name, the exact label keys every sample carries, how its
    value reads, and the value semantics prose. ``labels`` is the *complete* key set present on
    every sample of this metric — the consistency test asserts emitted label keys equal it."""

    name: str
    collector: str
    labels: tuple[str, ...]
    kind: MetricKind
    semantics: str


# The Native HA emitted contract. Order mirrors ``render_nativeha_state_prom`` so the two read
# side by side. Every entry here MUST be emitted by ``mqro.collectors.nativeha`` and carry exactly
# these label keys — enforced by the consistency test.
EMITTED: tuple[MetricSpec, ...] = (
    MetricSpec(
        name="cluster_quorate",
        collector=NATIVEHA,
        labels=("node",),
        kind=MetricKind.BOOLEAN,
        semantics="1 when the Native HA quorum is met (current votes >= majority), else 0. "
        "Omitted entirely when the quorum count is unknown — never a false green.",
    ),
    MetricSpec(
        name="cluster_nha_quorum",
        collector=NATIVEHA,
        labels=("node",),
        kind=MetricKind.GAUGE,
        semantics="Current Native HA quorum vote count (the numerator of QUORUM(x/y)).",
    ),
    MetricSpec(
        name="cluster_node_online",
        collector=NATIVEHA,
        labels=("node", "member"),
        kind=MetricKind.BOOLEAN,
        semantics="1 when the instance holds a known role; 0 when its role is Unknown "
        "(down or transitioning).",
    ),
    MetricSpec(
        name="cluster_nha_role",
        collector=NATIVEHA,
        labels=("node", "member", "role"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); the instance's Native HA role rides the "
        "`role` label (Active / Leader / Replica / Unknown).",
    ),
    MetricSpec(
        name="cluster_nha_role_code",
        collector=NATIVEHA,
        labels=("node", "member"),
        kind=MetricKind.ENUM,
        semantics="Numeric role code for the cockpit instance matrix: 2=Active (runs the QM), "
        "3=Leader (healthy CRR replication leader), 1=Replica, 0=Unknown/other.",
    ),
    MetricSpec(
        name="cluster_nha_insync",
        collector=NATIVEHA,
        labels=("node", "member"),
        kind=MetricKind.BOOLEAN,
        semantics="1 when the instance's log is in sync with the leader (INSYNC(yes)), else 0.",
    ),
    MetricSpec(
        name="cluster_nha_hastatus",
        collector=NATIVEHA,
        labels=("node", "member", "status"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); the instance's HASTATUS rides the `status` "
        "label (Normal / Abnormal / ...).",
    ),
    MetricSpec(
        name="cluster_nha_hastatus_ok",
        collector=NATIVEHA,
        labels=("node", "member"),
        kind=MetricKind.BOOLEAN,
        semantics="1 when HASTATUS is Normal, else 0.",
    ),
    MetricSpec(
        name="cluster_resource_owner",
        collector=NATIVEHA,
        labels=("node", "resource", "holder"),
        kind=MetricKind.STATE,
        semantics="Emitted (value 1) only for the member running the queue manager (ROLE Active). "
        "`resource` is the QM name, `holder` the owning member.",
    ),
    MetricSpec(
        name="cluster_nha_group_role",
        collector=NATIVEHA,
        labels=("node", "group", "role"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); a Native HA group's GRPROLE rides the `role` "
        "label (Live / Recovery / ...).",
    ),
    MetricSpec(
        name="cluster_nha_group_status",
        collector=NATIVEHA,
        labels=("node", "group", "status"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); a Native HA group's GRSTATUS rides the "
        "`status` label.",
    ),
    MetricSpec(
        name="cluster_nha_connected",
        collector=NATIVEHA,
        labels=("node", "group"),
        kind=MetricKind.BOOLEAN,
        semantics="1 when a recovery group is connected to its live group (CRR CONNGRP(yes)), "
        "else 0. Emitted only for groups that report the facet (the recovery group).",
    ),
    MetricSpec(
        name="cluster_nha_group_insync",
        collector=NATIVEHA,
        labels=("node", "group"),
        kind=MetricKind.BOOLEAN,
        semantics="1 when a recovery group is in sync with its live group (INSYNC(yes)), else 0. "
        "Emitted only for groups that report the facet.",
    ),
    MetricSpec(
        name="cluster_nha_group_backlog",
        collector=NATIVEHA,
        labels=("node", "group"),
        kind=MetricKind.GAUGE,
        semantics="CRR replication backlog as a message count (never seconds). Emitted only for "
        "groups that report a numeric BACKLOG; a BACKLOG(Unknown) omits the sample.",
    ),
    MetricSpec(
        name="cluster_state_last_write_timestamp",
        collector=NATIVEHA,
        labels=("node", "source"),
        kind=MetricKind.TIMESTAMP,
        semantics="Unix time of the last fresh write, one sample per source that produced a "
        "reading this tick. A stalled source stops appearing, so its timestamp never advances "
        "and staleness alerting fires — never a republished last-known value.",
    ),
    # The Pacemaker/Corosync/DRBD emitted contract. Order mirrors ``render_cluster_state_prom`` so
    # the two read side by side. Every entry here MUST be emitted by ``mqro.collectors.cluster`` and
    # carry exactly these label keys — enforced by the consistency test.
    MetricSpec(
        name="cluster_quorate",
        collector=PACEMAKER,
        labels=("node",),
        kind=MetricKind.BOOLEAN,
        semantics="1 when the Pacemaker cluster has quorum (crm_mon current_dc with_quorum), "
        "else 0.",
    ),
    MetricSpec(
        name="cluster_node_online",
        collector=PACEMAKER,
        labels=("node", "member"),
        kind=MetricKind.BOOLEAN,
        semantics="1 when a cluster member is online (crm_mon node online), else 0.",
    ),
    MetricSpec(
        name="cluster_node_unclean",
        collector=PACEMAKER,
        labels=("node", "member"),
        kind=MetricKind.BOOLEAN,
        semantics="1 when a member is UNCLEAN (crm_mon node unclean — pending fence), else 0.",
    ),
    MetricSpec(
        name="cluster_resource_started",
        collector=PACEMAKER,
        labels=("node", "resource"),
        kind=MetricKind.BOOLEAN,
        semantics="1 when a resource-group member is Started, else 0. `resource` is the crm_mon "
        "resource id (e.g. the queue manager's Pacemaker resource); top-level STONITH resources "
        "are excluded.",
    ),
    MetricSpec(
        name="cluster_resource_owner",
        collector=PACEMAKER,
        labels=("node", "resource", "holder"),
        kind=MetricKind.STATE,
        semantics="Emitted (value 1) only for a placed resource; `resource` is the crm_mon "
        "resource id and `holder` the node running it. An unplaced resource emits no owner line.",
    ),
    MetricSpec(
        name="cluster_fence_count",
        collector=PACEMAKER,
        labels=("node", "member"),
        kind=MetricKind.GAUGE,
        semantics="Count of fence actions in stonith history for a member; 0 is the clean baseline "
        "emitted for every known member (so a clean cluster is green, not grey no-data).",
    ),
    MetricSpec(
        name="cluster_iscsi_sessions",
        collector=PACEMAKER,
        labels=("node",),
        kind=MetricKind.GAUGE,
        semantics="Count of active iSCSI sessions on this node (iscsiadm -m session).",
    ),
    MetricSpec(
        name="cluster_daemon_up",
        collector=PACEMAKER,
        labels=("node", "unit"),
        kind=MetricKind.BOOLEAN,
        semantics="1 when a cluster daemon unit is systemctl-active (corosync/pacemaker/drbd), "
        "else 0. A unit absent on this node reads 0.",
    ),
    MetricSpec(
        name="cluster_drbd_role",
        collector=PACEMAKER,
        labels=("node", "resource", "role"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); a DRBD resource's local role rides the `role` "
        "label (Primary / Secondary / Unknown).",
    ),
    MetricSpec(
        name="cluster_drbd_disk",
        collector=PACEMAKER,
        labels=("node", "resource", "disk"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); a DRBD resource's local disk state rides the "
        "`disk` label (UpToDate / Outdated / Diskless / ...).",
    ),
    MetricSpec(
        name="cluster_drbd_conn",
        collector=PACEMAKER,
        labels=("node", "resource", "conn"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); a DRBD resource's connection state rides the "
        "`conn` label (Connected / StandAlone / ... — the integrity-light trigger).",
    ),
    MetricSpec(
        name="cluster_drbd_resync_pct",
        collector=PACEMAKER,
        labels=("node", "resource"),
        kind=MetricKind.GAUGE,
        semantics="DRBD resync completion percent; 100 when in sync (no resync in progress).",
    ),
    MetricSpec(
        name="cluster_drbd_out_of_sync_bytes",
        collector=PACEMAKER,
        labels=("node", "resource"),
        kind=MetricKind.GAUGE,
        semantics="DRBD out-of-sync byte lag (the RPO signal); 0 when fully replicated.",
    ),
    MetricSpec(
        name="cluster_state_last_write_timestamp",
        collector=PACEMAKER,
        labels=("node", "source"),
        kind=MetricKind.TIMESTAMP,
        semantics="Unix time of the last fresh write, one sample per source (crm/stonith/iscsi/"
        "drbd/daemons) that produced a reading this tick. A stalled source stops appearing, so its "
        "timestamp never advances and staleness alerting fires — never a republished value.",
    ),
    # The RDQM emitted contract. Order mirrors ``render_rdqm_state_prom`` so the two read side by
    # side. Every entry here MUST be emitted by ``mqro.collectors.rdqm`` and carry exactly these
    # label keys — enforced by the consistency test. Several families are SHARED with the sibling
    # collectors (cluster_quorate, cluster_node_online, cluster_resource_owner, cluster_drbd_*, and
    # the last-write timestamp) and are declared once more here for the RDQM slice; RDQM-only facets
    # carry cluster_rdqm_* names.
    MetricSpec(
        name="cluster_quorate",
        collector=RDQM,
        labels=("node",),
        kind=MetricKind.BOOLEAN,
        semantics="1 when the RDQM HA group status is Normal, else 0 (RDQM has no vote count; HA "
        "health is the quorum proxy the shared cockpit reads).",
    ),
    MetricSpec(
        name="cluster_rdqm_ha_status_ok",
        collector=RDQM,
        labels=("node",),
        kind=MetricKind.BOOLEAN,
        semantics="1 when this node's HA status is Normal, else 0.",
    ),
    MetricSpec(
        name="cluster_rdqm_ha_status",
        collector=RDQM,
        labels=("node", "status"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); this node's HA status rides the `status` label "
        "(Normal / Degraded / ...).",
    ),
    MetricSpec(
        name="cluster_rdqm_role",
        collector=RDQM,
        labels=("node", "member", "role"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); the local node's HA role rides the `role` label "
        "(Primary / Secondary). Omitted when the role is unknown (never faked).",
    ),
    MetricSpec(
        name="cluster_rdqm_role_code",
        collector=RDQM,
        labels=("node", "member"),
        kind=MetricKind.ENUM,
        semantics="Numeric HA role code for the cockpit instance matrix: 2=Primary (runs the QM), "
        "1=Secondary (healthy standby), 0=Unknown/other.",
    ),
    MetricSpec(
        name="cluster_rdqm_qm_running",
        collector=RDQM,
        labels=("node", "member"),
        kind=MetricKind.BOOLEAN,
        semantics="1 when the queue manager status is Running on this node, else 0.",
    ),
    MetricSpec(
        name="cluster_node_online",
        collector=RDQM,
        labels=("node", "member"),
        kind=MetricKind.BOOLEAN,
        semantics="1 when a member's HA status is Normal, else 0 (down/degraded/not-available).",
    ),
    MetricSpec(
        name="cluster_rdqm_member_status",
        collector=RDQM,
        labels=("node", "member", "status"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); each member's HA status rides the `status` label.",
    ),
    MetricSpec(
        name="cluster_rdqm_floating_ip",
        collector=RDQM,
        labels=("node", "ip", "interface"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); the HA floating IP and its interface. Emitted "
        "only for the node actually RUNNING the QM, so a DR pair never shows two active VIPs.",
    ),
    MetricSpec(
        name="cluster_rdqm_location",
        collector=RDQM,
        labels=("node", "kind", "holder"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); an HA location — `kind` is current/preferred, "
        "`holder` the resolved node name (`This node` resolves to the reporting node).",
    ),
    MetricSpec(
        name="cluster_resource_owner",
        collector=RDQM,
        labels=("node", "resource", "holder"),
        kind=MetricKind.STATE,
        semantics="Emitted (value 1) only for the node RUNNING the queue manager. `resource` is "
        "the QM name, `holder` the resolved current HA location.",
    ),
    MetricSpec(
        name="cluster_rdqm_dr_role",
        collector=RDQM,
        labels=("node", "role"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); this node's DR role rides the `role` label "
        "(Primary / Secondary). Omitted when there is no DR block.",
    ),
    MetricSpec(
        name="cluster_rdqm_dr_role_code",
        collector=RDQM,
        labels=("node",),
        kind=MetricKind.ENUM,
        semantics="Numeric DR role code for the per-site chip: 2=Primary (LIVE site), 3=Secondary "
        "(RECOVERY site), 0=Unknown/other.",
    ),
    MetricSpec(
        name="cluster_rdqm_dr_status",
        collector=RDQM,
        labels=("node", "status"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); this node's DR status rides the `status` label. A "
        "`See <primary>` pointer (printed by non-QM-running nodes) is skipped, never emitted.",
    ),
    MetricSpec(
        name="cluster_rdqm_dr_status_ok",
        collector=RDQM,
        labels=("node",),
        kind=MetricKind.BOOLEAN,
        semantics="1 when this node's DR status is Normal, else 0. Emitted only for a real DR "
        "status (the `See <primary>` pointer emits neither the status nor this flag).",
    ),
    MetricSpec(
        name="cluster_drbd_role",
        collector=RDQM,
        labels=("node", "resource", "role"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); a DRBD resource's local role rides the `role` "
        "label (Primary / Secondary / Unknown).",
    ),
    MetricSpec(
        name="cluster_drbd_disk",
        collector=RDQM,
        labels=("node", "resource", "disk"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); a DRBD resource's local disk state rides the "
        "`disk` label (UpToDate / Outdated / Diskless / ...).",
    ),
    MetricSpec(
        name="cluster_drbd_conn",
        collector=RDQM,
        labels=("node", "resource", "conn"),
        kind=MetricKind.STATE,
        semantics="Info series (value always 1); a DRBD resource's connection state rides the "
        "`conn` label (Connected / StandAlone / ... — the integrity-light trigger).",
    ),
    MetricSpec(
        name="cluster_drbd_resync_pct",
        collector=RDQM,
        labels=("node", "resource"),
        kind=MetricKind.GAUGE,
        semantics="DRBD resync completion percent; 100 when in sync (no resync in progress).",
    ),
    MetricSpec(
        name="cluster_drbd_out_of_sync_bytes",
        collector=RDQM,
        labels=("node", "resource"),
        kind=MetricKind.GAUGE,
        semantics="DRBD out-of-sync byte lag (the RPO signal); 0 when fully replicated.",
    ),
    MetricSpec(
        name="cluster_rdqm_pm_online",
        collector=RDQM,
        labels=("node", "member"),
        kind=MetricKind.BOOLEAN,
        semantics="1 when a member is Pacemaker-online (crm_mon node online), else 0. This is the "
        "resource-manager view rdqmadm wraps — distinct from the HA `cluster_node_online`.",
    ),
    MetricSpec(
        name="cluster_rdqm_pm_state",
        collector=RDQM,
        labels=("node", "member", "resource"),
        kind=MetricKind.ENUM,
        semantics="Numeric Pacemaker resource-state code per (member, resource) of the QM stack "
        "(qmrdqm + DRBD clones + floating IP): 2=Started/Promoted, 1=Unpromoted, 0=Stopped/none.",
    ),
    MetricSpec(
        name="cluster_rdqm_failcount",
        collector=RDQM,
        labels=("node", "member"),
        kind=MetricKind.GAUGE,
        semantics="The QM resource's Pacemaker fail-count on a member; 1000000 is INFINITY. "
        "Exposes a banned/failed QM that rdqmstatus alone reports as HA-Normal.",
    ),
    MetricSpec(
        name="cluster_rdqm_qm_startable",
        collector=RDQM,
        labels=("node", "member"),
        kind=MetricKind.BOOLEAN,
        semantics="1 when the QM fail-count on a member is below INFINITY (Pacemaker can still "
        "start it there), else 0 (banned).",
    ),
    MetricSpec(
        name="cluster_rdqm_node_ready",
        collector=RDQM,
        labels=("node", "member"),
        kind=MetricKind.BOOLEAN,
        semantics="1 when a member is BOTH Pacemaker-online AND startable (a usable host); 0 "
        "otherwise, so a node online-but-banned reads red, not a misleading green.",
    ),
    MetricSpec(
        name="cluster_state_last_write_timestamp",
        collector=RDQM,
        labels=("node", "source"),
        kind=MetricKind.TIMESTAMP,
        semantics="Unix time of the last fresh write, one sample per source (rdqmstatus/drbd/crm) "
        "that produced a reading this tick. A stalled source stops appearing, so its timestamp "
        "never advances and staleness alerting fires — never a republished value.",
    ),
)


def by_collector(collector: str) -> tuple[MetricSpec, ...]:
    """Every :class:`MetricSpec` a given collector emits, in declaration order."""
    return tuple(spec for spec in EMITTED if spec.collector == collector)


def metric_names(collector: str | None = None) -> frozenset[str]:
    """The set of emitted metric names — all of them, or just one collector's when given."""
    specs = EMITTED if collector is None else by_collector(collector)
    return frozenset(spec.name for spec in specs)


def labels_of(name: str) -> tuple[str, ...]:
    """The exact label keys the named metric carries. Raises :class:`KeyError` if undeclared."""
    for spec in EMITTED:
        if spec.name == name:
            return spec.labels
    raise KeyError(name)
