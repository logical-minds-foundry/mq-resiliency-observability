from __future__ import annotations

from pathlib import Path

from mqro.collectors import cluster
from mqro.config import Profile

FIXTURES = Path(__file__).parent / "fixtures" / "cluster"


# ---------------------------------------------------------------------------
# parse_crm — quorum, node states, and resource placement (de-hardcoded group scope).
# ---------------------------------------------------------------------------
def test_parse_crm_extracts_quorum_nodes_and_resource_placement():
    # real capture: QM group currently on pcmk-a3; top-level fence_* resources present
    out = cluster.parse_crm((FIXTURES / "crm_mon.xml").read_text())
    assert out["quorate"] is True
    assert out["nodes"]["pcmk-a2"] == {"online": True, "standby": False, "unclean": False}
    assert out["resources"]["mq_qm"] == {"state": "Started", "node": "pcmk-a3"}
    assert out["resources"]["mq_fs"]["node"] == "pcmk-a3"
    # every resource GROUP is collected (no hardcoded group id); top-level STONITH is still excluded
    assert set(out["resources"]) == {"mq_fs", "mq_vip", "mq_vip_ext", "mq_qm"}
    assert "fence_pcmk-a1" not in out["resources"]


def test_parse_crm_marks_offline_node_and_unplaced_resource():
    xml = """<pacemaker-result>
      <summary><current_dc with_quorum="false"/></summary>
      <nodes><node name="pcmk-a2" online="false" standby="false" unclean="true"/></nodes>
      <resources><group id="mq_group">
        <resource id="mq_qm" role="Stopped" active="false"/>
      </group></resources>
    </pacemaker-result>"""
    out = cluster.parse_crm(xml)
    assert out["quorate"] is False
    assert out["nodes"]["pcmk-a2"]["unclean"] is True
    assert out["resources"]["mq_qm"] == {"state": "Stopped", "node": None}


def test_parse_crm_without_current_dc_is_not_quorate():
    out = cluster.parse_crm("<pacemaker-result><nodes/><resources/></pacemaker-result>")
    assert out["quorate"] is False
    assert out["nodes"] == {}
    assert out["resources"] == {}


def test_parse_crm_reports_resources_from_any_group_not_just_mq_group():
    # two differently-named groups -> both are reported; the lab's mq_group hardcode is gone
    xml = """<pacemaker-result>
      <summary><current_dc with_quorum="true"/></summary>
      <nodes><node name="n1" online="true" standby="false" unclean="false"/></nodes>
      <resources>
        <resource id="fence_n1" role="Started"><node name="n1"/></resource>
        <group id="alpha_group">
          <resource id="alpha_qm" role="Started"><node name="n1"/></resource>
        </group>
        <group id="beta_group"><resource id="beta_qm" role="Stopped"/></group>
      </resources>
    </pacemaker-result>"""
    out = cluster.parse_crm(xml)
    assert set(out["resources"]) == {"alpha_qm", "beta_qm"}  # both groups; fence_n1 excluded
    assert out["resources"]["alpha_qm"]["node"] == "n1"
    assert out["resources"]["beta_qm"]["node"] is None


# ---------------------------------------------------------------------------
# parse_stonith / parse_iscsi / parse_daemons.
# ---------------------------------------------------------------------------
def test_parse_stonith_counts_recent_fence_actions_per_node():
    text = (
        "Stonith history for cluster mqpcmk:\n"  # non-matching header -> skipped
        "pcmk-a2 was reset (off) by pcmk-a1 at Sat Jun 14 12:04:01 2026\n"
        "pcmk-a2 was reset (on) by pcmk-a1 at Sat Jun 14 12:05:10 2026\n"
    )
    assert cluster.parse_stonith(text) == {"pcmk-a2": 2}


def test_parse_stonith_counts_fenced_wording_too():
    # the alternate " was fenced " phrasing must also count (the second branch of the guard)
    assert cluster.parse_stonith("pcmk-a3 was fenced by pcmk-a1 at ...\n") == {"pcmk-a3": 1}


def test_parse_stonith_empty_history_is_clean():
    assert cluster.parse_stonith("") == {}


def test_parse_iscsi_counts_sessions():
    text = "Target: san-a\ntcp: [1] 10.40.1.5:3260,1 iqn.lab:san-a\n"
    assert cluster.parse_iscsi(text) == 1


def test_parse_iscsi_no_sessions_is_zero():
    assert cluster.parse_iscsi("iscsiadm: No active sessions.\n") == 0


def test_parse_daemons_reads_systemctl_is_active_block():
    text = "active\nactive\nfailed\n"
    units = ["corosync", "pacemaker", "drbd"]
    assert cluster.parse_daemons(text, units) == {
        "corosync": True,
        "pacemaker": True,
        "drbd": False,
    }


def test_parse_daemons_missing_line_reads_as_down():
    # fewer is-active lines than units -> the missing unit reads False (fail-loud)
    assert cluster.parse_daemons("active\n", ["corosync", "pacemaker"]) == {
        "corosync": True,
        "pacemaker": False,
    }


# ---------------------------------------------------------------------------
# render_cluster_state_prom.
# ---------------------------------------------------------------------------
def test_render_cluster_section_emits_quorum_resource_and_timestamp():
    crm = {
        "quorate": True,
        "nodes": {"pcmk-a2": {"online": True, "standby": False, "unclean": False}},
        "resources": {"mq_qm": {"state": "Started", "node": "pcmk-a2"}},
    }
    out = cluster.render_cluster_state_prom(
        node="pcmk-a1",
        crm=crm,
        stonith={"pcmk-a2": 0},
        iscsi=2,
        daemons={"corosync": True, "pacemaker": True},
        drbd=None,
        now=1781455000,
        fresh_sources=("crm", "stonith", "iscsi", "daemons"),
    )
    assert 'cluster_quorate{node="pcmk-a1"} 1' in out
    assert 'cluster_node_online{node="pcmk-a1",member="pcmk-a2"} 1' in out
    assert 'cluster_resource_started{node="pcmk-a1",resource="mq_qm"} 1' in out
    assert 'cluster_resource_owner{node="pcmk-a1",resource="mq_qm",holder="pcmk-a2"} 1' in out
    assert 'cluster_iscsi_sessions{node="pcmk-a1"} 2' in out
    assert 'cluster_daemon_up{node="pcmk-a1",unit="corosync"} 1' in out
    assert 'cluster_fence_count{node="pcmk-a1",member="pcmk-a2"} 0' in out  # clean baseline
    assert 'cluster_state_last_write_timestamp{node="pcmk-a1",source="crm"} 1781455000' in out


def test_render_fence_baseline_zero_for_clean_members_and_count_for_fenced():
    crm = {
        "quorate": True,
        "nodes": {
            "pcmk-a1": {"online": True, "standby": False, "unclean": False},
            "pcmk-a2": {"online": False, "standby": False, "unclean": True},
        },
        "resources": {},
    }
    out = cluster.render_cluster_state_prom(
        node="pcmk-a1",
        crm=crm,
        stonith={"pcmk-a2": 3},
        iscsi=None,
        daemons={},
        drbd=None,
        now=1,
        fresh_sources=(),
    )
    assert 'cluster_fence_count{node="pcmk-a1",member="pcmk-a1"} 0' in out  # clean -> green
    assert 'cluster_fence_count{node="pcmk-a1",member="pcmk-a2"} 3' in out  # fenced -> red
    assert "cluster_iscsi_sessions" not in out  # iscsi None -> omitted


def test_render_omits_timestamp_for_stale_source():
    out = cluster.render_cluster_state_prom(
        node="pcmk-a1",
        crm=None,
        stonith={},
        iscsi=0,
        daemons={},
        drbd=None,
        now=1781455000,
        fresh_sources=("stonith", "iscsi", "daemons"),
    )
    assert "cluster_quorate" not in out  # crm None -> whole crm section omitted
    assert 'source="crm"' not in out
    assert 'cluster_iscsi_sessions{node="pcmk-a1"} 0' in out
    assert 'cluster_state_last_write_timestamp{node="pcmk-a1",source="stonith"} 1781455000' in out


def test_render_storage_section_emits_drbd_enums_and_rpo():
    drbd = {
        "r0": {
            "role": "Primary",
            "disk": "UpToDate",
            "conn": "StandAlone",
            "resync_pct": 42.0,
            "out_of_sync_bytes": 2202010,
        }
    }
    out = cluster.render_cluster_state_prom(
        node="san-a",
        crm=None,
        stonith=None,
        iscsi=None,
        daemons={"drbd": True},
        drbd=drbd,
        now=1781455000,
        fresh_sources=("drbd", "daemons"),
    )
    assert 'cluster_drbd_role{node="san-a",resource="r0",role="Primary"} 1' in out
    assert 'cluster_drbd_disk{node="san-a",resource="r0",disk="UpToDate"} 1' in out
    assert 'cluster_drbd_conn{node="san-a",resource="r0",conn="StandAlone"} 1' in out
    assert 'cluster_drbd_out_of_sync_bytes{node="san-a",resource="r0"} 2202010' in out
    assert 'cluster_drbd_resync_pct{node="san-a",resource="r0"} 42.0' in out


def test_render_covers_degraded_and_missing_value_branches():
    # offline+unclean node, stopped+unplaced resource, down daemon, drbd with no resync/RPO
    crm = {
        "quorate": False,
        "nodes": {"pcmk-a2": {"online": False, "standby": True, "unclean": True}},
        "resources": {"mq_qm": {"state": "Stopped", "node": None}},
    }
    drbd = {
        "r0": {
            "role": "Secondary",
            "disk": "Diskless",
            "conn": "Disconnected",
            "resync_pct": None,
            "out_of_sync_bytes": None,
        }
    }
    out = cluster.render_cluster_state_prom(
        node="pcmk-a2",
        crm=crm,
        stonith=None,
        iscsi=None,
        daemons={"drbd": False},
        drbd=drbd,
        now=1,
        fresh_sources=(),
    )
    assert 'cluster_quorate{node="pcmk-a2"} 0' in out
    assert 'cluster_node_online{node="pcmk-a2",member="pcmk-a2"} 0' in out
    assert 'cluster_node_unclean{node="pcmk-a2",member="pcmk-a2"} 1' in out
    assert 'cluster_resource_started{node="pcmk-a2",resource="mq_qm"} 0' in out
    assert "cluster_resource_owner" not in out  # node is None -> no owner line
    assert 'cluster_fence_count{node="pcmk-a2",member="pcmk-a2"} 0' in out  # stonith None -> {}
    assert 'cluster_daemon_up{node="pcmk-a2",unit="drbd"} 0' in out
    assert "cluster_drbd_resync_pct" not in out  # None -> omitted
    assert "cluster_drbd_out_of_sync_bytes" not in out  # None -> omitted
    assert "last_write_timestamp" not in out  # empty fresh_sources


# ---------------------------------------------------------------------------
# collect / run — probe orchestration and atomic textfile write.
# ---------------------------------------------------------------------------
def _fake_probe_all_fresh(cmd, timeout, ignore_rc=False):
    crm_xml = (FIXTURES / "crm_mon.xml").read_text()
    drbd_text = (FIXTURES / "drbd_status.txt").read_text()
    if cmd[0] == "crm_mon":
        return crm_xml
    if cmd[0] == "stonith_admin":
        return "0 events found\n"
    if cmd[0] == "iscsiadm":
        return "tcp: [1] 10.40.1.5:3260,1 iqn.lab:san-a\n"
    if cmd[0] == "drbdsetup":
        return drbd_text
    if cmd[0] == "systemctl":
        return "active\nactive\nactive\n"
    return None


def test_collect_renders_every_source_fresh(monkeypatch):
    monkeypatch.setattr(cluster, "probe", _fake_probe_all_fresh)
    body = cluster.collect("pcmk-a1", 1781455000)
    assert 'cluster_quorate{node="pcmk-a1"} 1' in body
    assert 'cluster_iscsi_sessions{node="pcmk-a1"} 1' in body
    assert 'cluster_drbd_role{node="pcmk-a1",resource="mqlun",role="Primary"} 1' in body
    assert 'cluster_daemon_up{node="pcmk-a1",unit="corosync"} 1' in body
    for source in ("crm", "stonith", "iscsi", "drbd", "daemons"):
        assert f'source="{source}"' in body


def test_collect_marks_every_source_stale_when_probes_time_out(monkeypatch):
    monkeypatch.setattr(cluster, "probe", lambda cmd, timeout, ignore_rc=False: None)
    body = cluster.collect("pcmk-a1", 1781455000)
    assert "cluster_quorate" not in body  # crm stale -> omitted
    assert "cluster_drbd_role" not in body  # drbd stale -> omitted
    assert "cluster_daemon_up" not in body  # daemons stale -> omitted
    assert "last_write_timestamp" not in body  # no source fresh


def test_run_writes_textfile_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(cluster, "probe", _fake_probe_all_fresh)
    prof = Profile(qm="QMPCMK", textfile_dir=tmp_path)
    out = cluster.run(prof, node="pcmk-a1", now=1781455000)
    text = out.read_text()
    assert out == tmp_path / "mqro_cluster_state.prom"
    assert 'cluster_quorate{node="pcmk-a1"} 1' in text
    assert 'cluster_drbd_role{node="pcmk-a1",resource="mqlun",role="Primary"} 1' in text
    assert 'source="drbd"' in text
    assert not (tmp_path / "mqro_cluster_state.prom.tmp").exists()  # atomic move cleaned up


def test_run_defaults_node_to_hostname_and_now_to_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(cluster, "probe", _fake_probe_all_fresh)
    monkeypatch.setattr(cluster.os, "uname", lambda: type("U", (), {"nodename": "pcmk-a2"})())
    monkeypatch.setattr(cluster.time, "time", lambda: 1781455999.0)
    prof = Profile(qm="QMPCMK", textfile_dir=tmp_path)
    out = cluster.run(prof)  # node + now both default
    text = out.read_text()
    assert 'cluster_quorate{node="pcmk-a2"} 1' in text
    assert 'cluster_state_last_write_timestamp{node="pcmk-a2",source="crm"} 1781455999' in text
