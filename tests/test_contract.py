"""Consistency-test harness for the emitted-metrics contract (spec §3.3, half 1).

The single-repo consistency invariant: **every metric named in the contract is one a collector
actually emits, and every metric a collector emits is one the contract names** — checked in both
directions, at both name and label-key granularity, so drift on either side fails here rather than
silently blanking a dashboard panel.

Scope today is the Native HA subset (slice 1). The harness is deliberately factored so the T14
dashboard work extends it two ways without reshaping it:

1. **More collectors.** ``_render_all(...)`` produces the emitted (name, label-keys) set for a
   collector from representative inputs; a new collector adds its own ``_render_all_<name>`` and a
   parametrized case in :func:`test_contract_matches_collector_emission`.
2. **The board side.** When the dashboards land, T14 adds a parser that harvests every ``cluster_*``
   metric a panel queries and asserts that set is a subset of :func:`contract.metric_names` — the
   "every panel query is a contract metric" half. :func:`emitted_metric_names` /
   :func:`emitted_label_keys` are the reusable primitives it builds on; the ``# T14:`` markers below
   flag the extension points.
"""

from __future__ import annotations

import re

from mqro import contract
from mqro.collectors import cluster, nativeha, rdqm

# A rendered sample line is ``name{k="v",...} value``. Capture the name and the raw label block.
_LINE = re.compile(r"^(?P<name>\w+)\{(?P<labels>[^}]*)\}\s")
_LABEL_KEY = re.compile(r"(\w+)=")


def emitted_metric_names(text: str) -> set[str]:
    """Every distinct metric name in a rendered textfile body."""
    return {m["name"] for line in text.splitlines() if (m := _LINE.match(line))}


def emitted_label_keys(text: str, name: str) -> set[tuple[str, ...]]:
    """The distinct label-key tuples seen across all samples of ``name`` (order preserved)."""
    seen: set[tuple[str, ...]] = set()
    for line in text.splitlines():
        m = _LINE.match(line)
        if m is not None and m["name"] == name:
            seen.add(tuple(_LABEL_KEY.findall(m["labels"])))
    return seen


def _render_all_nativeha() -> str:
    """Render one Native HA textfile exercising EVERY family in the Native HA contract.

    An Active member (owner + role-code 2), a Leader (role-code 3), and a Replica (role-code 1)
    cover the instance families and ``cluster_resource_owner``; a Live group plus a Recovery group
    carrying the CRR facets cover the group families; two fresh sources stamp the last-write family.
    """
    hax = {
        "quorum_current": 3,
        "quorum_total": 3,
        "group_role": "Live",
        "instances": {
            "nha-rhel-a1": {"role": "Active", "insync": True, "hastatus": "Normal"},
            "nha-rhel-b1": {"role": "Leader", "insync": True, "hastatus": "Normal"},
            "nha-rhel-a2": {"role": "Replica", "insync": True, "hastatus": "Normal"},
        },
    }
    grp = {
        "Live": {
            "role": "Live",
            "status": "Normal",
            "connected": None,
            "insync": None,
            "backlog": None,
        },
        "Recovery": {
            "role": "Recovery",
            "status": "Normal",
            "connected": True,
            "insync": True,
            "backlog": 0,
        },
    }
    return nativeha.render_nativeha_state_prom(
        node="nha-rhel-a1",
        qm="QMNATIVE",
        hax=hax,
        grp=grp,
        now=1781455000,
        fresh_sources=("nativeha_x", "nativeha_g"),
    )


def _render_all_cluster() -> str:
    """Render one Pacemaker/DRBD textfile exercising EVERY family in the Pacemaker contract.

    An online member plus an offline+unclean member cover the node families and the fence baseline;
    a Started+placed resource covers ``cluster_resource_started`` and ``cluster_resource_owner``; a
    non-zero iSCSI session count, three daemon units, and a DRBD resource carrying resync/RPO
    (non-None) cover the remaining families; five fresh sources stamp the last-write family.
    """
    crm = {
        "quorate": True,
        "nodes": {
            "pcmk-a1": {"online": True, "standby": False, "unclean": False},
            "pcmk-a2": {"online": False, "standby": False, "unclean": True},
        },
        "resources": {"mq_qm": {"state": "Started", "node": "pcmk-a1"}},
    }
    drbd = {
        "mqlun": {
            "role": "Primary",
            "disk": "UpToDate",
            "conn": "Connected",
            "resync_pct": 42.0,
            "out_of_sync_bytes": 2202010,
        }
    }
    return cluster.render_cluster_state_prom(
        node="pcmk-a1",
        crm=crm,
        stonith={"pcmk-a2": 3},
        iscsi=2,
        daemons={"corosync": True, "pacemaker": True, "drbd": True},
        drbd=drbd,
        now=1781455000,
        fresh_sources=("crm", "stonith", "iscsi", "drbd", "daemons"),
    )


def _render_all_rdqm() -> str:
    """Render one RDQM textfile exercising EVERY family in the RDQM contract.

    A running Primary with a floating IP, current+preferred locations, and a real DR block covers
    the HA/DR + owner families; a DRBD resource covers the ``cluster_drbd_*`` families; a crm node
    covers the ``cluster_rdqm_pm_*`` / failcount / startable / node_ready families; three fresh
    sources stamp the last-write family.
    """
    status = {
        "node": "rdqm-a1",
        "qm_status": "Running",
        "ha_role": "Primary",
        "ha_status": "Normal",
        "ha_current_location": "This node",
        "ha_preferred_location": "This node",
        "floating_ip": "10.10.1.100",
        "floating_ip_interface": "enp7s0",
        "dr_role": "Primary",
        "dr_status": "Normal",
        "members": {"rdqm-a1": {"ha_status": "Normal"}},
    }
    drbd = {
        "qmrdqm": {
            "role": "Primary",
            "disk": "UpToDate",
            "conn": "Connected",
            "resync_pct": 100.0,
            "out_of_sync_bytes": 0,
        }
    }
    crm = {
        "nodes": {"rdqm-a1": {"online": True}},
        "roles": {"qmrdqm": {"rdqm-a1": "Started"}},
        "failcount": {"rdqm-a1": 0},
    }
    return rdqm.render_rdqm_state_prom(
        node="rdqm-a1",
        qm="QMRDQM",
        status=status,
        drbd=drbd,
        crm=crm,
        now=1781455000,
        fresh_sources=("rdqmstatus", "drbd", "crm"),
    )


# collector id -> a body that emits its whole contract slice. T14: add other collectors here.
_RENDERERS = {
    contract.NATIVEHA: _render_all_nativeha,
    contract.PACEMAKER: _render_all_cluster,
    contract.RDQM: _render_all_rdqm,
}


def test_contract_matches_collector_emission():
    """Bidirectional name-level invariant, per collector: contract names == emitted names."""
    for collector, render in _RENDERERS.items():
        emitted = emitted_metric_names(render())
        declared = set(contract.metric_names(collector))
        assert emitted == declared, (
            f"{collector}: contract-only {declared - emitted}, emitted-only {emitted - declared}"
        )


def test_emitted_label_keys_match_the_contract():
    """Every emitted sample of a contract metric carries exactly the declared label keys."""
    for collector, render in _RENDERERS.items():
        text = render()
        for name in contract.metric_names(collector):
            declared = contract.labels_of(name)
            assert emitted_label_keys(text, name) == {declared}, (
                f"{name}: label keys drifted from the contract {declared}"
            )


def test_every_renderer_covers_a_known_collector():
    """Guard the scaffold: each renderer's collector is one the contract actually declares."""
    known = {spec.collector for spec in contract.EMITTED}
    assert set(_RENDERERS) <= known


def test_contract_version_is_declared():
    assert isinstance(contract.CONTRACT_VERSION, str)
    assert contract.CONTRACT_VERSION


def test_metric_names_all_equals_union_of_collectors():
    # metric_names() with no collector returns the whole contract; per-collector slices union to it
    all_names = contract.metric_names()
    per_collector = set().union(
        *(contract.metric_names(c) for c in {spec.collector for spec in contract.EMITTED})
    )
    assert all_names == per_collector
    assert contract.metric_names("no-such-collector") == frozenset()


def test_by_collector_partitions_the_contract():
    nha = contract.by_collector(contract.NATIVEHA)
    pcmk = contract.by_collector(contract.PACEMAKER)
    rdqm_slice = contract.by_collector(contract.RDQM)
    # the collectors' slices partition the whole contract: together they are EMITTED, and no spec
    # belongs to two (shared families are declared once PER collector, as distinct specs).
    assert set(nha) | set(pcmk) | set(rdqm_slice) == set(contract.EMITTED)
    assert set(nha).isdisjoint(pcmk)
    assert set(nha).isdisjoint(rdqm_slice)
    assert set(pcmk).isdisjoint(rdqm_slice)
    assert nha and pcmk and rdqm_slice  # every slice is non-empty
    assert contract.by_collector("no-such-collector") == ()


def test_labels_of_returns_declared_keys_and_raises_when_unknown():
    assert contract.labels_of("cluster_quorate") == ("node",)
    try:
        contract.labels_of("cluster_does_not_exist")
    except KeyError as exc:
        assert "cluster_does_not_exist" in str(exc)
    else:  # pragma: no cover - the call above must raise
        raise AssertionError("labels_of should raise KeyError on an undeclared metric")


def test_specs_are_well_formed():
    # every spec has a node label (the per-host label every series carries) and non-empty semantics
    for spec in contract.EMITTED:
        assert spec.labels[0] == "node"
        assert spec.semantics
        assert isinstance(spec.kind, contract.MetricKind)


# ── The board side (T14): every cluster_* a panel queries is one a collector emits ────────────
#
# The second half of the consistency invariant (see the module docstring's extension point #2):
# a rendered board's PromQL must never query a `cluster_*` family the collectors do not emit —
# that would silently blank a panel. Stock series (`ibmmq_*` / `node_*`) and the external app-SLA
# signal (`app_roundtrip_*`) are documented-as-external: they come from the stock exporter / the
# operator's own probes, are NOT in this repo's contract, and are explicitly allowed. The harness
# distinguishes the two: OUR families (cluster_*) must be emitted; EXTERNAL families are allowlisted
# by prefix, and any series selector matching neither fails loud (a typo'd or unaccounted family).

from mqro.dashboards import DashboardProfile  # noqa: E402
from mqro.dashboards.render import render_all  # noqa: E402

# A representative profile — its literals are irrelevant to the invariant (metric NAMES are what is
# checked, not label values), but a full render exercises every panel's query.
_BOARD_PROFILE = DashboardProfile(
    slug="harness",
    short="HRN",
    svc_short="SVC",
    site_a_group="hrn_a",
    site_b_group="hrn_b",
    qm_resource="hrn_qm",
    log_host_patterns=("hrn-.*",),
)

# Drop label values / Loki backtick bodies so they never contribute a false metric token.
_STRING = re.compile(r'"[^"]*"|`[^`]*`')
# A cluster_* metric token (our families — with or without a label matcher).
_CLUSTER_METRIC = re.compile(r"\bcluster_[a-zA-Z0-9_]+")
# A series selector: an identifier immediately followed by a `{` label matcher.
_SELECTOR = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)\s*\{")

# Series selectors whose family is allowed but external to this repo's contract.
_EXTERNAL_PREFIXES = ("ibmmq_", "node_", "app_roundtrip_")
_EXTERNAL_EXACT = frozenset({"up"})


def _panel_prom_exprs(board: dict) -> list[str]:
    """Every Prometheus PromQL expr a board's panels query, plus its annotation exprs. Loki log
    panels are skipped (their expr is a log selector, not PromQL)."""
    exprs: list[str] = []
    for panel in board.get("panels", []):
        if panel.get("type") == "logs":
            continue
        for target in panel.get("targets", []):
            ds = target.get("datasource") or {}
            if ds.get("type", "prometheus") == "prometheus":
                exprs.append(target["expr"])
    for ann in board.get("annotations", {}).get("list", []):
        if "expr" in ann:
            exprs.append(ann["expr"])
    return exprs


def board_cluster_metrics(board: dict) -> set[str]:
    """Every ``cluster_*`` (our-family) metric name a board's panels query."""
    found: set[str] = set()
    for expr in _panel_prom_exprs(board):
        found.update(_CLUSTER_METRIC.findall(_STRING.sub("", expr)))
    return found


def board_selector_names(board: dict) -> set[str]:
    """Every series-selector metric name (an identifier with a `{` matcher) a board queries."""
    found: set[str] = set()
    for expr in _panel_prom_exprs(board):
        found.update(_SELECTOR.findall(_STRING.sub("", expr)))
    return found


def _is_external(name: str) -> bool:
    return name in _EXTERNAL_EXACT or name.startswith(_EXTERNAL_PREFIXES)


def test_board_cluster_metrics_are_all_emitted():
    """Every ``cluster_*`` family a shipped board queries is one the contract declares a collector
    emits — the board→collector half of the consistency invariant."""
    declared = contract.metric_names()
    for uid, board in render_all(_BOARD_PROFILE).items():
        used = board_cluster_metrics(board)
        assert used <= declared, f"{uid}: queries un-emitted cluster metrics {used - declared}"


def test_board_selectors_are_ours_or_external():
    """Every series selector a board queries is either an OUR (cluster_*) family that must be
    emitted, or a documented-external stock/app family — never an unaccounted third thing."""
    declared = contract.metric_names()
    for uid, board in render_all(_BOARD_PROFILE).items():
        for name in board_selector_names(board):
            if name.startswith("cluster_"):
                assert name in declared, f"{uid}: cluster selector {name} is not emitted"
            else:
                assert _is_external(name), f"{uid}: selector {name} is neither ours nor external"


def test_cluster_board_actually_exercises_the_contract():
    """Guard the harness against passing vacuously: the cluster cockpit must query real cluster_*
    families (a broken render that emits none would otherwise trivially satisfy the subset test),
    while the stock-only boards query none of ours."""
    boards = render_all(_BOARD_PROFILE)
    cluster_board = boards[_BOARD_PROFILE.cluster_board_uid]
    assert board_cluster_metrics(cluster_board), "the cluster cockpit queries no cluster_* metrics"
    # the stock-only boards carry no dependency on our contract
    assert board_cluster_metrics(boards[_BOARD_PROFILE.qm_board_uid]) == set()
    assert board_cluster_metrics(boards[_BOARD_PROFILE.messaging_board_uid]) == set()
