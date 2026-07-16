from __future__ import annotations

from pathlib import Path

from mqro.collectors import rdqm
from mqro.config import Profile

FIXTURES = Path(__file__).parent / "fixtures" / "rdqm"


# ---------------------------------------------------------------------------
# parse_rdqmstatus
# ---------------------------------------------------------------------------
def test_parse_rdqmstatus_extracts_local_block_and_per_member_status():
    # real capture from rdqm-a1 (the QM-running primary): full detail block + a2/a3 footers
    out = rdqm.parse_rdqmstatus((FIXTURES / "rdqmstatus_m.txt").read_text())
    assert out["node"] == "rdqm-a1"
    assert out["qm_status"] == "Running"
    assert out["ha_role"] == "Primary"
    assert out["ha_status"] == "Normal"
    assert out["ha_current_location"] == "This node"
    assert out["ha_preferred_location"] == "This node"
    assert out["floating_ip"] == "10.10.1.100"
    assert out["floating_ip_interface"] == "enp7s0"
    assert out["dr_role"] == "Primary"
    assert out["dr_status"] == "Normal"
    # every block (local + footers) contributes a per-member HA status row
    assert set(out["members"]) == {"rdqm-a1", "rdqm-a2", "rdqm-a3"}
    assert out["members"]["rdqm-a1"] == {"ha_status": "Normal"}
    assert out["members"]["rdqm-a2"] == {"ha_status": "Normal"}


def test_parse_rdqmstatus_secondary_and_degraded_and_missing_fields():
    # a secondary node's view: QM running elsewhere, this node Secondary/Degraded, no DR/IP block
    text = (
        "Node:                rdqm-a2\n"
        "Queue manager status:Running elsewhere\n"
        "HA role:             Secondary\n"
        "HA status:           Degraded\n"
        "HA current location: rdqm-a1\n"
        "HA preferred location:rdqm-a1\n"
        "\n"
        "Node:                rdqm-a1\n"
        "HA status:           Normal\n"
        "\n"
        "Node:                rdqm-a3\n"
        "HA status:           Not available\n"
    )
    out = rdqm.parse_rdqmstatus(text)
    assert out["node"] == "rdqm-a2"
    assert out["qm_status"] == "Running elsewhere"
    assert out["ha_role"] == "Secondary"
    assert out["ha_status"] == "Degraded"
    assert out["ha_current_location"] == "rdqm-a1"  # the QM runs on a1, not this node
    assert out["floating_ip"] is None  # secondary block carries no floating-IP fields
    assert out["dr_role"] is None
    assert out["dr_status"] is None
    assert out["members"]["rdqm-a3"] == {"ha_status": "Not available"}


def test_parse_rdqmstatus_empty_and_preamble_lines_fabricate_nothing():
    # blank lines, a colon-less banner line, and a key before any Node: are all ignored;
    # nothing is fabricated (fail-loud), and unseen fields read None / Unknown.
    out = rdqm.parse_rdqmstatus("rdqmstatus for queue manager QMRDQM\n\nStray: value\n")
    assert out["node"] is None
    assert out["ha_role"] is None
    assert out["ha_status"] == "Unknown"
    assert out["qm_status"] is None
    assert out["members"] == {}


# ---------------------------------------------------------------------------
# render_rdqm_state_prom — HA/DR facets from rdqmstatus
# ---------------------------------------------------------------------------
def test_render_emits_owner_quorum_role_and_per_member_metrics():
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
        "members": {
            "rdqm-a1": {"ha_status": "Normal"},
            "rdqm-a2": {"ha_status": "Normal"},
            "rdqm-a3": {"ha_status": "Normal"},
        },
    }
    out = rdqm.render_rdqm_state_prom(
        node="rdqm-a1",
        qm="QMRDQM",
        status=status,
        drbd=None,
        now=1781455000,
        fresh_sources=("rdqmstatus",),
    )
    # group HA health: Normal -> quorate 1, status ok 1, status label series
    assert 'cluster_quorate{node="rdqm-a1"} 1' in out
    assert 'cluster_rdqm_ha_status_ok{node="rdqm-a1"} 1' in out
    assert 'cluster_rdqm_ha_status{node="rdqm-a1",status="Normal"} 1' in out
    # local node role (Primary -> code 2) and QM-running (1 here)
    assert 'cluster_rdqm_role{node="rdqm-a1",member="rdqm-a1",role="Primary"} 1' in out
    assert 'cluster_rdqm_role_code{node="rdqm-a1",member="rdqm-a1"} 2' in out
    assert 'cluster_rdqm_qm_running{node="rdqm-a1",member="rdqm-a1"} 1' in out
    # per member: online (1 if Normal) + the status label series
    assert 'cluster_node_online{node="rdqm-a1",member="rdqm-a2"} 1' in out
    assert 'cluster_rdqm_member_status{node="rdqm-a1",member="rdqm-a3",status="Normal"} 1' in out
    # floating IP (addr + iface)
    assert 'cluster_rdqm_floating_ip{node="rdqm-a1",ip="10.10.1.100",interface="enp7s0"} 1' in out
    # current/preferred location resolve "This node" -> the reporting node
    assert 'cluster_rdqm_location{node="rdqm-a1",kind="current",holder="rdqm-a1"} 1' in out
    assert 'cluster_rdqm_location{node="rdqm-a1",kind="preferred",holder="rdqm-a1"} 1' in out
    # the resource owner is the resolved current location
    assert 'cluster_resource_owner{node="rdqm-a1",resource="QMRDQM",holder="rdqm-a1"} 1' in out
    # DR role/status (Primary -> live, code 2) + ok flag
    assert 'cluster_rdqm_dr_role{node="rdqm-a1",role="Primary"} 1' in out
    assert 'cluster_rdqm_dr_role_code{node="rdqm-a1"} 2' in out
    assert 'cluster_rdqm_dr_status{node="rdqm-a1",status="Normal"} 1' in out
    assert 'cluster_rdqm_dr_status_ok{node="rdqm-a1"} 1' in out
    assert (
        'cluster_state_last_write_timestamp{node="rdqm-a1",source="rdqmstatus"} 1781455000' in out
    )


def test_render_secondary_degraded_codes_and_resolves_remote_location():
    # a secondary's report: Degraded (not Normal), QM not running locally, location -> a1,
    # DR role Secondary -> recovery code 3, DR status Degraded -> ok 0.
    status = {
        "node": "rdqm-b1",
        "qm_status": "Running elsewhere",
        "ha_role": "Secondary",
        "ha_status": "Degraded",
        "ha_current_location": "rdqm-a1",
        "ha_preferred_location": "rdqm-a1",
        "floating_ip": None,
        "floating_ip_interface": None,
        "dr_role": "Secondary",
        "dr_status": "Degraded",
        "members": {
            "rdqm-b1": {"ha_status": "Degraded"},
            "rdqm-b2": {"ha_status": "Not available"},
        },
    }
    out = rdqm.render_rdqm_state_prom(
        node="rdqm-b1", qm="QMRDQM", status=status, drbd=None, now=1, fresh_sources=()
    )
    assert 'cluster_quorate{node="rdqm-b1"} 0' in out  # Degraded -> not quorate
    assert 'cluster_rdqm_ha_status_ok{node="rdqm-b1"} 0' in out
    assert 'cluster_rdqm_role_code{node="rdqm-b1",member="rdqm-b1"} 1' in out  # Secondary
    assert 'cluster_rdqm_qm_running{node="rdqm-b1",member="rdqm-b1"} 0' in out  # not Running
    assert 'cluster_node_online{node="rdqm-b1",member="rdqm-b2"} 0' in out  # Not available
    assert "cluster_rdqm_floating_ip" not in out  # no floating IP on a secondary
    # the location is still published (failback signal), but a node not RUNNING the QM is
    # NOT its owner — else a DR setup shows the QM "running on" both sites' HA primaries
    assert 'cluster_rdqm_location{node="rdqm-b1",kind="current",holder="rdqm-a1"} 1' in out
    assert "cluster_resource_owner" not in out  # qm not running here -> not the owner
    assert 'cluster_rdqm_dr_role_code{node="rdqm-b1"} 3' in out  # Secondary -> recovery
    assert 'cluster_rdqm_dr_status_ok{node="rdqm-b1"} 0' in out  # Degraded is a REAL status
    assert "last_write_timestamp" not in out  # empty fresh_sources


def test_render_standby_primary_does_not_own_qm_or_publish_vip():
    # the DR-standby site's HA primary reports "HA current location: This node" and a configured
    # floating IP, but the QM is NOT running there. It must not be shown as the owner, nor
    # publish its (inactive) VIP — both belong only to the node actually running the QM (#287).
    status = {
        "node": "rdqm-b2",
        "qm_status": "Ended",  # not "Running" -> standby site
        "ha_role": "Primary",  # HA primary OF THE STANDBY SITE
        "ha_status": "Normal",
        "ha_current_location": "This node",
        "ha_preferred_location": "This node",
        "floating_ip": "10.10.2.100",  # site-B VIP, configured but inactive
        "floating_ip_interface": "enp7s0",
        "dr_role": "Secondary",
        "dr_status": "Normal",
        "members": {"rdqm-b2": {"ha_status": "Normal"}},
    }
    out = rdqm.render_rdqm_state_prom(
        node="rdqm-b2", qm="QMRDQM", status=status, drbd=None, now=1, fresh_sources=()
    )
    assert 'cluster_rdqm_qm_running{node="rdqm-b2",member="rdqm-b2"} 0' in out
    assert "cluster_resource_owner" not in out  # not running -> not the owner
    assert "cluster_rdqm_floating_ip" not in out  # not running -> its VIP isn't the active one
    # the location is still emitted (it is this site's HA primary)
    assert 'cluster_rdqm_location{node="rdqm-b2",kind="current",holder="rdqm-b2"} 1' in out


def test_render_skips_see_pointer_dr_status():
    # the non-QM-running nodes print `DR status: See <primary>` — a pointer, not a real status.
    # Treating it as != Normal would falsely drive the DR card to Degraded; skip it entirely so
    # only the authoritative node's real status (Normal/Disconnected/...) counts (#287).
    status = {
        "node": "rdqm-a2",
        "qm_status": "Running elsewhere",
        "ha_role": "Secondary",
        "ha_status": "Normal",
        "ha_current_location": "rdqm-a1",
        "ha_preferred_location": "rdqm-a1",
        "floating_ip": None,
        "floating_ip_interface": None,
        "dr_role": "Primary",
        "dr_status": "See rdqm-a1",  # pointer, not a status
        "members": {"rdqm-a2": {"ha_status": "Normal"}},
    }
    out = rdqm.render_rdqm_state_prom(
        node="rdqm-a2", qm="QMRDQM", status=status, drbd=None, now=1, fresh_sources=()
    )
    assert "cluster_rdqm_dr_status" not in out  # "See ..." -> neither status nor ok emitted
    assert 'cluster_rdqm_dr_role{node="rdqm-a2",role="Primary"} 1' in out  # role IS real


def test_render_unknown_role_and_absent_optional_blocks():
    # no ha_role / no location / no DR: those metric families are omitted (never faked),
    # and an unknown role codes 0.
    status = {
        "node": "rdqm-a1",
        "qm_status": None,
        "ha_role": None,
        "ha_status": "Unknown",
        "ha_current_location": None,
        "ha_preferred_location": None,
        "floating_ip": None,
        "floating_ip_interface": None,
        "dr_role": None,
        "dr_status": None,
        "members": {},
    }
    out = rdqm.render_rdqm_state_prom(
        node="rdqm-a1", qm="QMRDQM", status=status, drbd=None, now=1, fresh_sources=()
    )
    assert 'cluster_quorate{node="rdqm-a1"} 0' in out  # Unknown -> not Normal -> 0
    assert "cluster_rdqm_role{" not in out  # no role label series when ha_role is None
    assert 'cluster_rdqm_role_code{node="rdqm-a1",member="rdqm-a1"} 0' in out  # Unknown -> 0
    assert "cluster_rdqm_location" not in out  # no location block
    assert "cluster_resource_owner" not in out  # no current location -> no owner
    assert "cluster_rdqm_dr_role" not in out  # no DR block
    assert "cluster_rdqm_dr_status" not in out


def test_render_current_location_without_running_publishes_location_only():
    # a current location present but the QM not running here: the location series is still published
    # (the failback signal); cluster_resource_owner is withheld (only the running node owns it).
    status = {
        "node": "rdqm-a2",
        "qm_status": "Running elsewhere",
        "ha_role": "Secondary",
        "ha_status": "Normal",
        "ha_current_location": "rdqm-a1",
        "ha_preferred_location": None,  # no preferred block -> that branch stays unemitted
        "floating_ip": None,
        "floating_ip_interface": None,
        "dr_role": None,
        "dr_status": None,
        "members": {"rdqm-a2": {"ha_status": "Normal"}},
    }
    out = rdqm.render_rdqm_state_prom(
        node="rdqm-a2", qm="QMRDQM", status=status, drbd=None, now=1, fresh_sources=()
    )
    assert 'cluster_rdqm_location{node="rdqm-a2",kind="current",holder="rdqm-a1"} 1' in out
    assert "cluster_resource_owner" not in out
    assert 'kind="preferred"' not in out  # pref_loc None -> preferred location omitted


# ---------------------------------------------------------------------------
# render_rdqm_state_prom — DRBD (imported parse_drbd) + Pacemaker (crm)
# ---------------------------------------------------------------------------
def test_render_projects_both_drbd_resources():
    # reuse the shared drbd.parse_drbd for the HA (qmrdqm) + DR (qmrdqm.dr) resources
    from mqro.collectors.drbd import parse_drbd

    drbd = parse_drbd((FIXTURES / "drbdsetup_status.txt").read_text())
    out = rdqm.render_rdqm_state_prom(
        node="rdqm-a1", qm="QMRDQM", status=None, drbd=drbd, now=1, fresh_sources=("drbd",)
    )
    assert 'cluster_drbd_role{node="rdqm-a1",resource="qmrdqm",role="Primary"} 1' in out
    assert 'cluster_drbd_disk{node="rdqm-a1",resource="qmrdqm",disk="UpToDate"} 1' in out
    assert 'cluster_drbd_out_of_sync_bytes{node="rdqm-a1",resource="qmrdqm"} 0' in out
    # the DR resource is projected under its own resource label
    assert 'cluster_drbd_role{node="rdqm-a1",resource="qmrdqm.dr",role="Primary"} 1' in out
    assert 'cluster_drbd_resync_pct{node="rdqm-a1",resource="qmrdqm.dr"} 100.0' in out
    # status was None -> no rdqm/HA series leaked in
    assert "cluster_rdqm_ha_status" not in out
    assert "cluster_quorate" not in out


def test_parse_crm_banned_site_exposes_stopped_qm_and_infinity_failcount():
    crm = rdqm.parse_crm((FIXTURES / "crm_mon.xml").read_text())
    assert crm["nodes"]["rdqm-b2"]["online"] is True
    # the QM application stack is Stopped everywhere on the banned site -> no node placement
    assert crm["roles"].get("qmrdqm", {}) == {}
    # the DRBD HA clone is still healthy: promoted on b2, unpromoted on the others
    assert crm["roles"]["p_drbd_qmrdqm"]["rdqm-b2"] == "Promoted"
    assert crm["roles"]["p_drbd_qmrdqm"]["rdqm-b1"] == "Unpromoted"
    # fail-count is pacemaker INFINITY (1000000) on all three -> banned
    assert crm["failcount"]["rdqm-b1"] == 1000000
    assert crm["failcount"]["rdqm-b3"] == 1000000


def test_parse_crm_healthy_site_places_qm_and_zero_failcount():
    crm = rdqm.parse_crm((FIXTURES / "crm_mon_healthy.xml").read_text())
    assert crm["roles"]["qmrdqm"]["rdqm-a1"] == "Started"  # QM running on a1
    assert crm["roles"]["p_drbd_qmrdqm"]["rdqm-a1"] == "Promoted"
    assert crm["failcount"].get("rdqm-a1", 0) == 0  # fail-count None -> 0


def test_parse_crm_failcount_handles_infinity_string_garbage_and_missing():
    # crm_mon may print the literal "INFINITY"; absent/garbage fail-counts read 0 (fail-loud:
    # a node we can't read a failcount for is assumed startable, not silently banned); a plain
    # integer parses as itself.
    assert rdqm._parse_failcount("INFINITY") == 1000000
    assert rdqm._parse_failcount(None) == 0
    assert rdqm._parse_failcount("garbage") == 0
    assert rdqm._parse_failcount("42") == 42


def test_parse_crm_without_resources_section_is_safe():
    # a degenerate crm_mon (nodes only, no <resources>/<node_history>) must not crash and must
    # fabricate nothing — roles/failcount empty, nodes still read.
    crm = rdqm.parse_crm(
        '<pacemaker-result><nodes><node name="x" online="true"/></nodes></pacemaker-result>'
    )
    assert crm["nodes"]["x"]["online"] is True
    assert crm["roles"] == {}
    assert crm["failcount"] == {}


def test_render_pacemaker_metrics_expose_the_banned_qm():
    crm = rdqm.parse_crm((FIXTURES / "crm_mon.xml").read_text())
    out = rdqm.render_rdqm_state_prom(
        node="rdqm-b1", qm="QMRDQM", status=None, drbd=None, crm=crm, now=1, fresh_sources=("crm",)
    )
    assert 'cluster_rdqm_pm_online{node="rdqm-b1",member="rdqm-b2"} 1' in out
    # the QM primitive is Stopped on every node (state 0)
    assert 'cluster_rdqm_pm_state{node="rdqm-b1",member="rdqm-b1",resource="qmrdqm"} 0' in out
    # DRBD HA: promoted on b2 (2), unpromoted on b1 (1)
    assert (
        'cluster_rdqm_pm_state{node="rdqm-b1",member="rdqm-b2",resource="p_drbd_qmrdqm"} 2' in out
    )
    assert (
        'cluster_rdqm_pm_state{node="rdqm-b1",member="rdqm-b1",resource="p_drbd_qmrdqm"} 1' in out
    )
    # the loud signal: INFINITY fail-count -> NOT startable
    assert 'cluster_rdqm_failcount{node="rdqm-b1",member="rdqm-b3"} 1000000' in out
    assert 'cluster_rdqm_qm_startable{node="rdqm-b1",member="rdqm-b3"} 0' in out
    # a node that is pacemaker-online but BANNED is NOT a usable host -> node_ready 0 (the
    # board shows this red, not a misleading green "online") (#287)
    assert 'cluster_rdqm_node_ready{node="rdqm-b1",member="rdqm-b1"} 0' in out


def test_render_pacemaker_metrics_healthy_site_is_startable():
    crm = rdqm.parse_crm((FIXTURES / "crm_mon_healthy.xml").read_text())
    out = rdqm.render_rdqm_state_prom(
        node="rdqm-a1", qm="QMRDQM", status=None, drbd=None, crm=crm, now=1, fresh_sources=("crm",)
    )
    # QM Started on a1 -> state 2 (active)
    assert 'cluster_rdqm_pm_state{node="rdqm-a1",member="rdqm-a1",resource="qmrdqm"} 2' in out
    assert 'cluster_rdqm_failcount{node="rdqm-a1",member="rdqm-a1"} 0' in out
    assert 'cluster_rdqm_qm_startable{node="rdqm-a1",member="rdqm-a1"} 1' in out
    # online AND startable -> a usable host
    assert 'cluster_rdqm_node_ready{node="rdqm-a1",member="rdqm-a1"} 1' in out


def test_render_pacemaker_offline_member_is_not_ready():
    # an offline member (crm_mon online=false): pm_online 0, and node_ready 0 even though it is
    # startable (fail-count 0). Covers the online=False branch and the offline path of node_ready.
    crm = {
        "nodes": {"rdqm-a1": {"online": False}},
        "roles": {},
        "failcount": {},
    }
    out = rdqm.render_rdqm_state_prom(
        node="rdqm-a1", qm="QMRDQM", status=None, drbd=None, crm=crm, now=1, fresh_sources=("crm",)
    )
    assert 'cluster_rdqm_pm_online{node="rdqm-a1",member="rdqm-a1"} 0' in out
    # a resource with no placement reads state 0
    assert 'cluster_rdqm_pm_state{node="rdqm-a1",member="rdqm-a1",resource="qmrdqm"} 0' in out
    assert 'cluster_rdqm_failcount{node="rdqm-a1",member="rdqm-a1"} 0' in out
    assert 'cluster_rdqm_qm_startable{node="rdqm-a1",member="rdqm-a1"} 1' in out  # 0 < INFINITY
    assert 'cluster_rdqm_node_ready{node="rdqm-a1",member="rdqm-a1"} 0' in out  # offline


def test_render_without_crm_emits_no_pacemaker_metrics():
    out = rdqm.render_rdqm_state_prom(
        node="rdqm-a1", qm="QMRDQM", status=None, drbd=None, crm=None, now=1, fresh_sources=()
    )
    assert "cluster_rdqm_pm_" not in out  # crm probe stale -> pacemaker section reads STALE
    assert "cluster_rdqm_failcount" not in out


# ---------------------------------------------------------------------------
# collect / run — probe wiring, freshness, and atomic textfile write
# ---------------------------------------------------------------------------
def _route_probe(rtext: str, dtext: str, crmtext: str):
    """A fake base.probe routed by argv[0]: rdqmstatus / drbdsetup / crm_mon."""

    def fake_probe(cmd, timeout, **_kw):
        if "drbdsetup" in cmd[0]:
            return dtext
        if "crm_mon" in cmd[0]:
            return crmtext
        return rtext

    return fake_probe


def test_collect_renders_every_fresh_source(monkeypatch):
    rtext = (FIXTURES / "rdqmstatus_m.txt").read_text()
    dtext = (FIXTURES / "drbdsetup_status.txt").read_text()
    crmtext = (FIXTURES / "crm_mon_healthy.xml").read_text()
    monkeypatch.setattr(rdqm, "probe", _route_probe(rtext, dtext, crmtext))
    body = rdqm.collect("rdqm-a1", "QMRDQM", 1781455000)
    assert 'cluster_resource_owner{node="rdqm-a1",resource="QMRDQM",holder="rdqm-a1"} 1' in body
    assert 'cluster_drbd_role{node="rdqm-a1",resource="qmrdqm",role="Primary"} 1' in body
    assert 'cluster_rdqm_qm_startable{node="rdqm-a1",member="rdqm-a1"} 1' in body  # pacemaker
    assert 'source="rdqmstatus"' in body
    assert 'source="drbd"' in body
    assert 'source="crm"' in body


def test_collect_reads_rdqmstatus_with_merged_stderr(monkeypatch):
    # rdqmstatus writes its report to STDERR under the systemd timer, so its probe MUST merge
    # stderr (merge_stderr=True) or the parse sees an empty string; the other two must NOT.
    seen: dict[str, bool] = {}

    def fake_probe(cmd, timeout, **kw):
        seen[cmd[0]] = kw.get("merge_stderr", False)
        # only rdqmstatus returns a body (its report); the other two go STALE so their parsers
        # (which expect XML / drbd text) are never handed this rdqmstatus fixture.
        return "Node:   rdqm-a1\nHA status:  Normal\n" if "rdqmstatus" in cmd[0] else None

    monkeypatch.setattr(rdqm, "probe", fake_probe)
    rdqm.collect("rdqm-a1", "QMRDQM", 1)
    assert seen[rdqm._RDQMSTATUS_BIN] is True  # rdqmstatus: stderr folded in
    assert seen["drbdsetup"] is False  # clean stdout, keep streams separate
    assert seen["crm_mon"] is False


def test_collect_marks_sources_stale_when_probes_time_out(monkeypatch):
    monkeypatch.setattr(rdqm, "probe", lambda cmd, timeout, **_kw: None)
    body = rdqm.collect("rdqm-a1", "QMRDQM", 1)
    assert "cluster_quorate" not in body  # rdqmstatus stale -> omitted
    assert "cluster_drbd" not in body  # drbd stale -> omitted
    assert "cluster_rdqm_pm_" not in body  # crm stale -> pacemaker omitted
    assert "last_write_timestamp" not in body  # nothing fresh


def test_run_writes_textfile_atomically(tmp_path, monkeypatch):
    rtext = (FIXTURES / "rdqmstatus_m.txt").read_text()
    dtext = (FIXTURES / "drbdsetup_status.txt").read_text()
    crmtext = (FIXTURES / "crm_mon_healthy.xml").read_text()
    monkeypatch.setattr(rdqm, "probe", _route_probe(rtext, dtext, crmtext))
    profile = Profile(qm="QMRDQM", textfile_dir=tmp_path)
    out = rdqm.run(profile, node="rdqm-a1", now=1781455000)
    assert out == tmp_path / "mqro_rdqm_state.prom"
    text = out.read_text()
    assert 'cluster_resource_owner{node="rdqm-a1",resource="QMRDQM",holder="rdqm-a1"} 1' in text
    assert 'cluster_drbd_role{node="rdqm-a1",resource="qmrdqm",role="Primary"} 1' in text
    assert 'cluster_rdqm_qm_startable{node="rdqm-a1",member="rdqm-a1"} 1' in text
    assert not (tmp_path / "mqro_rdqm_state.prom.tmp").exists()  # atomic move cleaned up


def test_run_defaults_node_to_hostname_and_now_to_clock(tmp_path, monkeypatch):
    rtext = (FIXTURES / "rdqmstatus_m.txt").read_text()
    monkeypatch.setattr(
        rdqm, "probe", lambda cmd, timeout, **_kw: rtext if "rdqmstatus" in cmd[0] else None
    )
    monkeypatch.setattr(rdqm.os, "uname", lambda: type("U", (), {"nodename": "rdqm-a1"})())
    monkeypatch.setattr(rdqm.time, "time", lambda: 1781455999.0)
    profile = Profile(qm="QMRDQM", textfile_dir=tmp_path)
    out = rdqm.run(profile)
    text = out.read_text()
    assert 'cluster_quorate{node="rdqm-a1"} 1' in text
    assert (
        'cluster_state_last_write_timestamp{node="rdqm-a1",source="rdqmstatus"} 1781455999' in text
    )
