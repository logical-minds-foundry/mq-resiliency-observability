from __future__ import annotations

import pytest

from mqro import detect
from mqro.detect import Collector, ProbeResults, UnresolvableStackError


# ---------------------------------------------------------------------------
# resolve() — the precedence/exclusion framework (a truth table).
#
# `expected is None` rows assert fail-loud. The RDQM row is the load-bearing
# precedence case: an RDQM node answers BOTH rdqmstatus AND crm_mon, and RDQM
# must WIN and SUPPRESS the generic Pacemaker collector. The RDQM *runtime probe*
# is now wired (see test_probe_rdqm_true_when_tooling_present and the end-to-end
# test_detect_rdqm_node_activates_rdqm_only_and_suppresses_cluster).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("probes", "expected"),
    [
        # RDQM present -> rdqmstate ONLY. crm_mon also answering (its bundled Pacemaker) must
        # NOT add clusterstate. This is the precedence + exclusion invariant.
        (ProbeResults(rdqm=True, pacemaker=True), {Collector.RDQM}),
        (ProbeResults(rdqm=True, nativeha=True, pacemaker=True), {Collector.RDQM}),
        # Native HA node.
        (ProbeResults(nativeha=True), {Collector.NATIVEHA}),
        # Standalone Pacemaker: crm_mon present AND not RDQM.
        (ProbeResults(pacemaker=True), {Collector.PACEMAKER}),
        # Precedence when several answer but no RDQM: Native HA outranks standalone Pacemaker.
        (ProbeResults(nativeha=True, pacemaker=True), {Collector.NATIVEHA}),
        # Genuinely unresolvable: nothing recognized -> fail loud.
        (ProbeResults(rdqm=False, nativeha=False, pacemaker=False), None),
        # All-unprobed (the T3 seams return None) is ALSO unresolvable -> fail loud, never a
        # silent empty activation.
        (ProbeResults(), None),
    ],
)
def test_resolve_precedence_table(probes, expected):
    if expected is None:
        with pytest.raises(UnresolvableStackError):
            detect.resolve(probes)
    else:
        assert detect.resolve(probes) == frozenset(expected)


def test_resolve_rdqm_suppresses_pacemaker_even_though_crm_mon_answers():
    # explicit, standalone assertion of the exclusion half of the rule (spec §3.2 item 1).
    result = detect.resolve(ProbeResults(rdqm=True, pacemaker=True))
    assert result == frozenset({Collector.RDQM})
    assert Collector.PACEMAKER not in result


def test_unresolvable_error_surfaces_the_probe_signal():
    # fail-loud must be *detectable*: the error carries which probes answered, and the None
    # placeholders are visible in the message (not swallowed).
    probes = ProbeResults(rdqm=None, nativeha=False, pacemaker=None)
    with pytest.raises(UnresolvableStackError) as excinfo:
        detect.resolve(probes)
    assert excinfo.value.probes is probes
    assert "no recognized MQ HA/DR stack" in str(excinfo.value)
    assert "nativeha=False" in str(excinfo.value)


# ---------------------------------------------------------------------------
# detect() — explicit override always wins; else gather + resolve.
# ---------------------------------------------------------------------------
def test_detect_override_always_wins_without_probing(monkeypatch):
    # an override must short-circuit BEFORE any probe runs (probing would shell out).
    def boom() -> ProbeResults:
        raise AssertionError("detect() must not probe when an override is given")

    monkeypatch.setattr(detect, "gather_probes", boom)
    assert detect.detect(override={Collector.PACEMAKER}) == frozenset({Collector.PACEMAKER})


def test_detect_explicit_empty_override_activates_nothing(monkeypatch):
    # an explicit empty set is a deliberate "activate nothing", distinct from a detection gap;
    # it still short-circuits detection (None, not empty, triggers detection).
    monkeypatch.setattr(
        detect, "gather_probes", lambda: (_ for _ in ()).throw(AssertionError("no probe"))
    )
    assert detect.detect(override=set()) == frozenset()


def test_detect_nativeha_environment_activates_nativehastate(monkeypatch):
    # a Native-HA node: dspmq -o nativeha succeeds; pacemaker probe False, rdqm tooling absent
    # -> detection resolves to nativehastate (Native HA outranks standalone Pacemaker anyway).
    monkeypatch.setattr(detect, "probe_nativeha", lambda: True)
    monkeypatch.setattr(detect, "probe_pacemaker", lambda: False)
    monkeypatch.setattr(detect, "probe_rdqm", lambda: False)
    assert detect.detect() == frozenset({Collector.NATIVEHA})


def test_detect_standalone_pacemaker_environment_activates_clusterstate(monkeypatch):
    # a standalone Pacemaker node: not Native HA, crm_mon answers and it's not RDQM -> clusterstate.
    monkeypatch.setattr(detect, "probe_nativeha", lambda: False)
    monkeypatch.setattr(detect, "probe_pacemaker", lambda: True)
    monkeypatch.setattr(detect, "probe_rdqm", lambda: False)
    assert detect.detect() == frozenset({Collector.PACEMAKER})


def test_detect_rdqm_node_activates_rdqm_only_and_suppresses_cluster(monkeypatch):
    # the end-to-end RDQM precedence case: the rdqmstatus tooling is present, so probe_rdqm is True
    # AND probe_pacemaker is False even though crm_mon WOULD answer (RDQM's bundled Pacemaker).
    # detect() must activate rdqmstate ALONE — never the generic clusterstate collector.
    monkeypatch.setattr(detect, "_rdqm_tooling_present", lambda: True)
    monkeypatch.setattr(detect, "probe_nativeha", lambda: False)
    monkeypatch.setattr(detect, "probe", lambda cmd, timeout: "<pacemaker-result/>\n")
    result = detect.detect()
    assert result == frozenset({Collector.RDQM})
    assert Collector.PACEMAKER not in result


def test_detect_unknown_stack_fails_loud(monkeypatch):
    # nothing detected (nativeha fails, pacemaker False, rdqm tooling absent) -> refuse to guess.
    monkeypatch.setattr(detect, "probe_nativeha", lambda: False)
    monkeypatch.setattr(detect, "probe_pacemaker", lambda: False)
    monkeypatch.setattr(detect, "probe_rdqm", lambda: False)
    with pytest.raises(UnresolvableStackError):
        detect.detect()


# ---------------------------------------------------------------------------
# Probe seams.
# ---------------------------------------------------------------------------
def test_probe_nativeha_true_when_dspmq_succeeds(monkeypatch):
    # base.probe returns stdout (str) on success -> Native HA present.
    monkeypatch.setattr(detect, "probe", lambda cmd, timeout: "QMNATIVE ... running\n")
    assert detect.probe_nativeha() is True


def test_probe_nativeha_false_when_dspmq_fails(monkeypatch):
    # base.probe returns None on nonzero/timeout/OSError -> not a Native HA node.
    monkeypatch.setattr(detect, "probe", lambda cmd, timeout: None)
    assert detect.probe_nativeha() is False


def test_probe_rdqm_true_when_tooling_present(monkeypatch):
    # an RDQM node is marked by the rdqmstatus tooling being installed — the SAME marker
    # probe_pacemaker uses to exclude RDQM, so the two probes cannot disagree.
    monkeypatch.setattr(detect, "_rdqm_tooling_present", lambda: True)
    assert detect.probe_rdqm() is True


def test_probe_rdqm_false_when_tooling_absent(monkeypatch):
    monkeypatch.setattr(detect, "_rdqm_tooling_present", lambda: False)
    assert detect.probe_rdqm() is False


def test_probe_rdqm_and_pacemaker_are_mutually_consistent_on_an_rdqm_node(monkeypatch):
    # the load-bearing consistency invariant: the ONE rdqmstatus-tooling marker drives both probes,
    # so an RDQM node reads rdqm=True AND pacemaker=False together (never a disagreement). crm_mon
    # WOULD answer here (RDQM's bundled Pacemaker), but probe_pacemaker short-circuits first.
    monkeypatch.setattr(detect, "_rdqm_tooling_present", lambda: True)
    monkeypatch.setattr(
        detect, "probe", lambda cmd, timeout: (_ for _ in ()).throw(AssertionError("no crm probe"))
    )
    assert detect.probe_rdqm() is True
    assert detect.probe_pacemaker() is False


def test_probe_pacemaker_true_when_crm_mon_answers_and_not_rdqm(monkeypatch):
    # standalone Pacemaker: crm_mon answers (probe returns stdout) and the RDQM tooling is absent.
    monkeypatch.setattr(detect, "_rdqm_tooling_present", lambda: False)
    monkeypatch.setattr(detect, "probe", lambda cmd, timeout: "<pacemaker-result/>\n")
    assert detect.probe_pacemaker() is True


def test_probe_pacemaker_false_when_crm_mon_does_not_answer(monkeypatch):
    # crm_mon absent/failed (probe returns None) and not RDQM -> not a Pacemaker node.
    monkeypatch.setattr(detect, "_rdqm_tooling_present", lambda: False)
    monkeypatch.setattr(detect, "probe", lambda cmd, timeout: None)
    assert detect.probe_pacemaker() is False


def test_probe_pacemaker_false_on_rdqm_node_without_running_crm_mon(monkeypatch):
    # an RDQM node ALSO answers crm_mon, but its bundled Pacemaker must NOT activate the generic
    # collector: the RDQM-tooling marker short-circuits to False BEFORE crm_mon is even probed.
    monkeypatch.setattr(detect, "_rdqm_tooling_present", lambda: True)
    monkeypatch.setattr(
        detect, "probe", lambda cmd, timeout: (_ for _ in ()).throw(AssertionError("no crm probe"))
    )
    assert detect.probe_pacemaker() is False


def test_rdqm_tooling_present_reads_the_binary_path(monkeypatch, tmp_path):
    # present -> the rdqmstatus binary exists on disk; absent -> it does not.
    binary = tmp_path / "rdqmstatus"
    binary.write_text("")
    monkeypatch.setattr(detect, "_RDQMSTATUS_BIN", str(binary))
    assert detect._rdqm_tooling_present() is True
    monkeypatch.setattr(detect, "_RDQMSTATUS_BIN", str(tmp_path / "nope"))
    assert detect._rdqm_tooling_present() is False


def test_gather_probes_runs_every_probe(monkeypatch):
    monkeypatch.setattr(detect, "probe_nativeha", lambda: True)
    monkeypatch.setattr(detect, "probe_rdqm", lambda: None)
    monkeypatch.setattr(detect, "probe_pacemaker", lambda: False)
    assert detect.gather_probes() == ProbeResults(rdqm=None, nativeha=True, pacemaker=False)
