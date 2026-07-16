from __future__ import annotations

from pathlib import Path

from mqro.collectors import drbd

FIXTURES = Path(__file__).parent / "fixtures" / "drbd"


def test_parse_drbd_extracts_role_disk_conn_and_rpo_tail():
    # real `drbdsetup status --verbose --statistics` capture (healthy, in-sync, single resource)
    out = drbd.parse_drbd((FIXTURES / "drbd_status.txt").read_text())
    assert set(out) == {"mqlun"}
    mqlun = out["mqlun"]
    assert mqlun["role"] == "Primary"
    assert mqlun["disk"] == "UpToDate"  # local volume line, not the peer-disk token
    assert mqlun["conn"] == "Connected"
    assert mqlun["resync_pct"] == 100.0  # in-sync: no done: token
    assert mqlun["out_of_sync_bytes"] == 0


def test_parse_drbd_resync_tail_reads_done_and_out_of_sync():
    # a resyncing resource: done: gives the percentage, out-of-sync the RPO byte lag
    text = (
        "mqlun role:Primary suspended:no\n"
        "  volume:0 minor:0 disk:UpToDate\n"
        "  peer connection:Connected role:Secondary congested:no\n"
        "    volume:0 replication:SyncSource peer-disk:Inconsistent done:42.20\n"
        "        received:0 sent:99 out-of-sync:2202010 pending:0 unacked:0\n"
    )
    mqlun = drbd.parse_drbd(text)["mqlun"]
    assert mqlun["resync_pct"] == 42.20
    assert mqlun["out_of_sync_bytes"] == 2202010
    assert mqlun["conn"] == "Connected"


def test_parse_drbd_flags_standalone_split_brain():
    # StandAlone connection == the integrity-light trigger; no done: while disconnected
    text = (
        "mqlun role:Secondary suspended:no\n"
        "  volume:0 minor:0 disk:UpToDate\n"
        "  peer connection:StandAlone role:Unknown congested:no\n"
    )
    mqlun = drbd.parse_drbd(text)["mqlun"]
    assert mqlun["conn"] == "StandAlone"  # the integrity-light trigger
    assert mqlun["disk"] == "UpToDate"
    assert mqlun["resync_pct"] == 100.0  # no done: while disconnected
    assert mqlun["out_of_sync_bytes"] == 0


def test_parse_drbd_projects_both_ha_and_dr_resources():
    # real rdqm capture: two resources (HA qmrdqm across 3 nodes + DR qmrdqm.dr); the blank
    # line between blocks must not start or corrupt a resource, and each block is keyed by its
    # column-0 name.
    out = drbd.parse_drbd((FIXTURES / "drbdsetup_status.txt").read_text())
    assert set(out) == {"qmrdqm", "qmrdqm.dr"}
    ha = out["qmrdqm"]
    assert ha["role"] == "Primary"
    assert ha["disk"] == "UpToDate"
    assert ha["conn"] == "Connected"
    assert ha["out_of_sync_bytes"] == 0
    assert ha["resync_pct"] == 100.0
    dr = out["qmrdqm.dr"]
    assert dr["role"] == "Primary"
    assert dr["disk"] == "UpToDate"
    # the DR block carries no peer/connection line in this capture -> conn stays Unknown default
    assert dr["conn"] == "Unknown"
    assert dr["resync_pct"] == 100.0


def test_parse_drbd_empty_input_yields_no_resources():
    assert drbd.parse_drbd("") == {}


def test_parse_drbd_column0_line_without_role_is_not_a_resource():
    # a header/garbage line at column 0 without a role: token must not open a resource block
    # (the role guard on the compound condition), while a following real resource still parses.
    text = "# drbdsetup status\nmqlun role:Primary suspended:no\n  volume:0 minor:0 disk:UpToDate\n"
    out = drbd.parse_drbd(text)
    assert set(out) == {"mqlun"}
    assert out["mqlun"]["disk"] == "UpToDate"
