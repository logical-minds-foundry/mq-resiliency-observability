from __future__ import annotations

import subprocess

from mqro.collectors import base


def test_metric_line_formats_labels_and_value():
    line = base.metric_line("cluster_nha_quorum", {"node": "n1", "member": "m1"}, 3)
    assert line == 'cluster_nha_quorum{node="n1",member="m1"} 3'


def test_metric_line_with_no_labels():
    assert base.metric_line("up", {}, 1) == "up{} 1"


def test_last_write_lines_one_per_fresh_source():
    lines = base.last_write_lines("n1", ("nativeha_x", "nativeha_g"), 1781455000)
    assert lines == [
        'cluster_state_last_write_timestamp{node="n1",source="nativeha_x"} 1781455000',
        'cluster_state_last_write_timestamp{node="n1",source="nativeha_g"} 1781455000',
    ]


def test_last_write_lines_empty_when_nothing_fresh():
    # a source that went STALE must never republish its timestamp — no fresh sources, no lines
    assert base.last_write_lines("n1", (), 1781455000) == []


def test_probe_returns_stdout_on_success(monkeypatch):
    monkeypatch.setattr(
        base.subprocess,
        "run",
        lambda c, **k: subprocess.CompletedProcess(c, 0, "out\n", ""),
    )
    assert base.probe(["dspmq"], timeout=3) == "out\n"


def test_probe_returns_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        base.subprocess,
        "run",
        lambda c, **k: subprocess.CompletedProcess(c, 1, "", "boom"),
    )
    assert base.probe(["dspmq"], timeout=3) is None


def test_probe_returns_none_on_timeout(monkeypatch):
    monkeypatch.setattr(
        base.subprocess,
        "run",
        lambda c, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(c, k["timeout"])),
    )
    assert base.probe(["dspmq"], timeout=3) is None


def test_probe_returns_none_on_oserror(monkeypatch):
    monkeypatch.setattr(
        base.subprocess,
        "run",
        lambda c, **k: (_ for _ in ()).throw(OSError("no dspmq")),
    )
    assert base.probe(["dspmq"], timeout=3) is None


def test_probe_ignore_rc_returns_stdout_on_nonzero(monkeypatch):
    # `systemctl is-active` exits non-zero for an inactive unit, but its stdout is the real state;
    # ignore_rc keeps that reading (down != probe-failed). Default (ignore_rc=False) still -> None.
    monkeypatch.setattr(
        base.subprocess,
        "run",
        lambda c, **k: subprocess.CompletedProcess(c, 3, "inactive\n", ""),
    )
    assert base.probe(["systemctl"], timeout=2) is None
    assert base.probe(["systemctl"], timeout=2, ignore_rc=True) == "inactive\n"


def test_probe_ignore_rc_still_returns_none_on_timeout(monkeypatch):
    # ignore_rc suppresses the exit code, NOT a timeout: an unreachable probe is still STALE, never
    # mistaken for a down reading.
    monkeypatch.setattr(
        base.subprocess,
        "run",
        lambda c, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(c, k["timeout"])),
    )
    assert base.probe(["systemctl"], timeout=2, ignore_rc=True) is None


def test_write_textfile_is_atomic_and_cleans_up_tmp(tmp_path):
    out = tmp_path / "state.prom"
    base.write_textfile(out, "hello\n")
    assert out.read_text() == "hello\n"
    assert not (tmp_path / "state.prom.tmp").exists()  # temp file renamed away, not left behind


def test_write_textfile_overwrites_existing(tmp_path):
    out = tmp_path / "state.prom"
    base.write_textfile(out, "first\n")
    base.write_textfile(out, "second\n")
    assert out.read_text() == "second\n"
