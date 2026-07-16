"""Tests for the metadata-driven dashboard generator.

Three properties, to the repo's 100%-branch bar:

1. **Deterministic** — the same profile renders byte-identical JSON every time.
2. **Profile-driven** — nothing is hardcoded: a custom profile's identities appear in the output
   and the lab's old literals (NHARAPP / RDQMAPP / mq_qm / pcmk_a / prometheus / loki) do NOT.
3. **Well-formed** — every board carries the pinned uid, panels, and the expected shape.
"""

from __future__ import annotations

import dataclasses

import pytest

from mqro.dashboards import DashboardProfile
from mqro.dashboards.boards import primitives as prim
from mqro.dashboards.boards.cluster import render_cluster_board
from mqro.dashboards.boards.messaging import render_messaging_board
from mqro.dashboards.boards.qm import render_qm_board
from mqro.dashboards.render import dumps, render_all

# A profile whose every field is a distinctive non-lab value, so the profile-driven assertions can
# prove no lab literal leaked through (the QM names derive from short/svc_short the way the lab's
# stacks registry derives them, never a competing source).
PROFILE = DashboardProfile(
    slug="acme-arm",
    short="ACME",
    svc_short="ZED",
    site_a_group="acme_left",
    site_b_group="acme_right",
    qm_resource="acme_qm_res",
    log_host_patterns=("acme-.*", "vault-.*"),
    title="ACME Infra",
    datasource_uid="ds-prom-xyz",
    logs_uid="ds-loki-xyz",
)

# Every board render entry point, keyed for parametrization.
_RENDERERS = {
    "qm": render_qm_board,
    "messaging": render_messaging_board,
    "cluster": render_cluster_board,
}

# Lab literals that must never survive de-hardcoding — if any appears, a constant was left baked in.
# NB: bare "prometheus" / "loki" are the datasource *types* Grafana requires and legitimately
# appear; the hardcoded-UID form (`"uid": "prometheus"`) is what must not survive a custom profile.
_LAB_LITERALS = [
    "NHARAPP",
    "RDQMAPP",
    "PCMKAPP",
    "PCMKSVC",
    "NHAUAPP",
    '"mq_qm"',
    "qmrdqm",
    "pcmk_a",
    "pcmk_b",
    "rdqm_a",
    "nha_rhel_a",
    '"uid": "prometheus"',
    '"uid": "loki"',
    "nha-rhel-",
    "rdqm-.*",
    "lab_network_",
    "lab_dns_",
]


@pytest.mark.parametrize("name", list(_RENDERERS))
def test_render_is_deterministic(name):
    render = _RENDERERS[name]
    assert dumps(render(PROFILE)) == dumps(render(PROFILE))


@pytest.mark.parametrize("name", list(_RENDERERS))
def test_no_lab_literal_survives(name):
    text = dumps(_RENDERERS[name](PROFILE))
    for literal in _LAB_LITERALS:
        assert literal not in text, f"{name}: lab literal {literal!r} leaked into the render"


@pytest.mark.parametrize("name", list(_RENDERERS))
def test_profile_datasource_is_threaded_everywhere(name):
    # the custom Prometheus datasource uid reaches every board (no board assumes "prometheus")
    text = dumps(_RENDERERS[name](PROFILE))
    assert "ds-prom-xyz" in text


def test_qm_board_shape():
    board = render_qm_board(PROFILE)
    assert board["uid"] == "mqro-qm-acme"
    assert board["tags"] == ["mqro", "qm", "acme-arm"]
    # the counterparty QM (svc_short → ZEDQM) shows up as the XMITQ / channel names
    text = dumps(board)
    assert "ZEDQM" in text
    assert '"templating"' not in text  # the qm board carries no template vars


def test_messaging_board_shape():
    board = render_messaging_board(PROFILE)
    assert board["uid"] == "mqro-messaging-acme-arm"
    # the App QM tile drills into the per-QM board uid
    text = dumps(board)
    assert "/d/mqro-qm-acme" in text
    # this stack's own request queue derives from short (ACME.SVC.REQUEST)
    assert "ACME.SVC.REQUEST" in text
    # the external app-SLA signal is present (documented-as-external)
    assert "app_roundtrip_total" in text


def test_cluster_board_shape():
    board = render_cluster_board(PROFILE)
    assert board["uid"] == "mqro-acme-arm-cluster"
    text = dumps(board)
    # the group selector is the profile's two sites, the QM resource its own id (JSON-escaped
    # quotes make the bare alternation/name the stable substring to assert on)
    assert "acme_left|acme_right" in text
    assert "acme_qm_res" in text
    # the log host patterns are the profile's
    assert "acme-.*|vault-.*" in text
    # the title banner spells out the profile title
    assert "ACME Infra" in text


def test_render_all_keys_by_uid():
    boards = render_all(PROFILE)
    assert set(boards) == {"mqro-qm-acme", "mqro-messaging-acme-arm", "mqro-acme-arm-cluster"}
    for uid, board in boards.items():
        assert board["uid"] == uid


# ── DashboardProfile derivations ──────────────────────────────────────────────


def test_profile_derivations():
    assert PROFILE.app_qm == "ACMEAPP"
    assert PROFILE.svc_qm == "ZEDQM"
    assert PROFILE.req_queue == "ACME.SVC.REQUEST"
    assert PROFILE.groups_selector == "acme_left|acme_right"
    assert PROFILE.host_selector == "acme-.*|vault-.*"
    assert PROFILE.board_title == "ACME Infra"
    assert PROFILE.qm_board_uid == "mqro-qm-acme"
    assert PROFILE.messaging_board_uid == "mqro-messaging-acme-arm"
    assert PROFILE.cluster_board_uid == "mqro-acme-arm-cluster"


def test_profile_defaults_and_fallbacks():
    # no log host patterns → the host selector opens up to .*; empty title → slug-derived fallback
    minimal = DashboardProfile(
        slug="min",
        short="M",
        svc_short="S",
        site_a_group="a",
        site_b_group="b",
        qm_resource="r",
    )
    assert minimal.host_selector == ".*"
    assert minimal.board_title == "min — cluster"
    assert minimal.datasource_uid == "prometheus"  # the default when not overridden
    assert minimal.logs_uid == "loki"


def test_profile_is_frozen():
    attr = "slug"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(PROFILE, attr, "other")


# ── primitive toolkit branches not fully driven by the board renders ──────────


def test_logs_panel_description_is_optional():
    with_desc = prim.logs_panel("t", "{}", "loki", 0, description="a note")
    without = prim.logs_panel("t", "{}", "loki", 0)
    assert with_desc["description"] == "a note"
    assert "description" not in without


def test_timeseries_unit_is_optional():
    with_unit = prim.timeseries("t", [], "ds", 0, 0, unit="bytes")
    without = prim.timeseries("t", [], "ds", 0, 0)
    assert with_unit["fieldConfig"]["defaults"]["unit"] == "bytes"
    assert "unit" not in without["fieldConfig"]["defaults"]


def test_stat_text_mode_name_sets_legend():
    named = prim.stat("t", "e", "ds", 0, 0, text_mode="name", name_label="ip")
    plain = prim.stat("t", "e", "ds", 0, 0)
    assert named["targets"][0]["legendFormat"] == "{{ip}}"
    assert "legendFormat" not in plain["targets"][0]
