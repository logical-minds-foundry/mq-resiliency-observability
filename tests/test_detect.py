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
# is stubbed until T12 (see test_probe_rdqm_is_stubbed), but the precedence LOGIC
# asserted here is the framework this task (T3) completes.
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
    # a Native-HA node: dspmq -o nativeha succeeds; rdqm/pacemaker seams return their None
    # placeholder -> detection resolves to nativehastate.
    monkeypatch.setattr(detect, "probe_nativeha", lambda: True)
    assert detect.detect() == frozenset({Collector.NATIVEHA})


def test_detect_unknown_stack_fails_loud(monkeypatch):
    # nothing detected (nativeha probe fails, rdqm/pcmk stubbed) -> refuse to guess.
    monkeypatch.setattr(detect, "probe_nativeha", lambda: False)
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


def test_probe_rdqm_is_stubbed_until_t12():
    # the RDQM runtime probe is a seam: it returns the not-yet-implemented placeholder None.
    # (The RDQM precedence LOGIC is already complete — see test_resolve_precedence_table.)
    assert detect.probe_rdqm() is None


def test_probe_pacemaker_is_stubbed_until_t13():
    assert detect.probe_pacemaker() is None


def test_gather_probes_runs_every_probe(monkeypatch):
    monkeypatch.setattr(detect, "probe_nativeha", lambda: True)
    monkeypatch.setattr(detect, "probe_rdqm", lambda: None)
    monkeypatch.setattr(detect, "probe_pacemaker", lambda: None)
    assert detect.gather_probes() == ProbeResults(rdqm=None, nativeha=True, pacemaker=None)
