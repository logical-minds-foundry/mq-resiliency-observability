"""Collector detection — precedence-and-exclusion resolution, not independent probes.

Which collector(s) a node should run is decided in two layers, mirroring spec §3.2:

- **Explicit configuration is the backbone.** An operator-supplied override *always* wins;
  detection is only a convenience layered on top and never overrides an explicit choice.
- **Auto-detection resolves by precedence, because the probes are not independent.** RDQM
  bundles its *own* Pacemaker/DRBD, so an RDQM node answers BOTH ``rdqmstatus`` AND
  ``crm_mon``. Treating those as independent "any-probe-that-answers" signals would
  double-activate the generic Pacemaker collector on every RDQM node and double-emit
  overlapping ``cluster_*`` series. So resolution is ordered and exclusive:

      1. RDQM        (``rdqmstatus`` present)      -> ``rdqmstate`` ONLY; the generic
                                                      Pacemaker collector is SUPPRESSED even
                                                      though ``crm_mon`` answers (RDQM's
                                                      Pacemaker is internal to it).
      2. Native HA   (``dspmq -o nativeha`` OK)    -> ``nativehastate``.
      3. Standalone  (``crm_mon`` present AND      -> ``clusterstate``.
         Pacemaker    NOT RDQM)

**Fail loud only on genuinely unresolvable stacks** — never on RDQM's legitimate
both-probes-answer case, which precedence handles. When nothing is recognized, detection
raises :class:`UnresolvableStackError` (a detectable signal) rather than guess a collector
and emit wrong-shaped metrics — the §8 boundary principle applied to detection.

The precedence/exclusion framework, the Native-HA probe, config-override, and fail-loud landed
with the framework (T3). :func:`probe_pacemaker` is now wired (T13): a determinate ``True`` /
``False``. Only :func:`probe_rdqm` remains a seam stubbed as ``None`` (wired up in T17) — but the
precedence LOGIC that suppresses Pacemaker under RDQM is already complete: that logic *is* the
framework.

Stdlib-only, like every collector: detection runs on the MQ/cluster node itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from mqro.collectors.base import probe

if TYPE_CHECKING:
    from collections.abc import Iterable


class Collector(StrEnum):
    """The collector identities detection can activate (the spec §2.2 module names)."""

    RDQM = "rdqmstate"
    NATIVEHA = "nativehastate"
    PACEMAKER = "clusterstate"


@dataclass(frozen=True)
class ProbeResults:
    """The outcome of each technology probe.

    Three-valued on purpose: ``True``/``False`` is a real determination, while ``None`` is
    the *not-yet-probed / not-yet-implemented* placeholder the remaining RDQM seam returns. A
    ``None`` never lets the resolver CLAIM that technology — an unprobed stack falls through to
    fail-loud rather than being silently assumed absent-and-fine.
    """

    rdqm: bool | None = None
    nativeha: bool | None = None
    pacemaker: bool | None = None


class UnresolvableStackError(RuntimeError):
    """No recognized MQ HA/DR stack — detection refuses to guess a collector (§3.2 fail-loud).

    Carries the probe results so the caller sees a detectable signal (which probes answered)
    instead of a silent empty activation.
    """

    def __init__(self, probes: ProbeResults) -> None:
        super().__init__(
            "mqro detect: no recognized MQ HA/DR stack "
            f"(rdqm={probes.rdqm}, nativeha={probes.nativeha}, pacemaker={probes.pacemaker}); "
            "refusing to guess a collector — set an explicit override"
        )
        self.probes = probes


# The node-level Native-HA probe: `dspmq -o nativeha` (no -m) succeeds only where MQ Native HA
# is configured. dspmq must run as the mqm user, so shell through `su - mqm -c` with the
# absolute path (matching the collector's own probe convention in collectors/nativeha.py).
_DSPMQ_NATIVEHA = ["su", "-", "mqm", "-c", "/opt/mqm/bin/dspmq -o nativeha"]


def probe_nativeha(*, timeout: int = 3) -> bool:
    """Real probe: ``True`` when ``dspmq -o nativeha`` succeeds (this is a Native-HA node)."""
    return probe(_DSPMQ_NATIVEHA, timeout) is not None


def probe_rdqm() -> bool | None:
    """Seam STUB — returns the not-yet-implemented placeholder ``None`` (wired up in T17/#17).

    The precedence LOGIC for RDQM (win + suppress Pacemaker) already lives in :func:`resolve`;
    only this runtime detection of an RDQM node is deferred.
    """
    return None


# The standalone-Pacemaker probe: `crm_mon` answering is the cluster signal. An RDQM node ALSO
# answers crm_mon (RDQM bundles its own Pacemaker), so the probe must exclude RDQM — the presence
# of the RDQM tooling (`rdqmstatus`) is that marker. This keeps the generic Pacemaker collector off
# RDQM nodes independently of :func:`probe_rdqm` (still a seam), so no double-activation slips
# through the window before #17 wires the RDQM probe.
_CRM_MON = ["crm_mon", "--one-shot", "--output-as=xml"]
_RDQMSTATUS_BIN = "/opt/mqm/bin/rdqmstatus"


def _rdqm_tooling_present() -> bool:
    """``True`` when the RDQM tooling (``rdqmstatus``) is installed — the marker of an RDQM node."""
    return Path(_RDQMSTATUS_BIN).exists()


def probe_pacemaker(*, timeout: int = 3) -> bool:
    """Real probe: ``True`` when this is a standalone Pacemaker node — ``crm_mon`` answers AND the
    node is not RDQM. Returns a determinate ``False`` (never ``None``) on an RDQM node or where
    crm_mon does not answer, so the resolver never mistakes it for an unprobed seam.
    """
    if _rdqm_tooling_present():
        return False
    return probe(_CRM_MON, timeout) is not None


def gather_probes() -> ProbeResults:
    """Run every technology probe once and collect the results (stubs included)."""
    return ProbeResults(
        rdqm=probe_rdqm(),
        nativeha=probe_nativeha(),
        pacemaker=probe_pacemaker(),
    )


def resolve(probes: ProbeResults) -> frozenset[Collector]:
    """Precedence-and-exclusion core: map probe results to the collector set to activate.

    Ordered early-returns ARE the exclusion: RDQM returns before Pacemaker is ever considered,
    so an RDQM node (which answers both probes) activates ``rdqmstate`` alone and never
    double-activates ``clusterstate``. A ``None`` (unprobed) is falsy here, so it can never win
    — it falls through toward fail-loud rather than being assumed present.
    """
    if probes.rdqm:
        return frozenset({Collector.RDQM})  # suppresses PACEMAKER even though crm_mon answers
    if probes.nativeha:
        return frozenset({Collector.NATIVEHA})
    if probes.pacemaker:
        return frozenset({Collector.PACEMAKER})
    raise UnresolvableStackError(probes)


def detect(override: Iterable[Collector] | None = None) -> frozenset[Collector]:
    """Resolve the collectors to activate: explicit ``override`` ALWAYS wins over detection.

    Passing ``override`` short-circuits before any probe runs (including an explicit empty set,
    which means "activate nothing" — a deliberate operator choice, not a detection gap). With
    no override, gather the probes and resolve by precedence, failing loud on an unknown stack.
    """
    if override is not None:
        return frozenset(override)
    return resolve(gather_probes())
