"""Metadata-driven Grafana dashboard generator (spec §3.3, half 2 — the board side).

The lab's boards baked their identities in as constants: QM names (``NHARAPP`` / ``RDQMAPP``),
Ansible group selectors (``groups=~"pcmk_a|pcmk_b"``), Pacemaker/DRBD resource names (``mq_qm``),
host-name patterns (``pcmk-.*``), and datasource UIDs. This package extracts every one of those
into a :class:`~mqro.dashboards.profile.DashboardProfile`, so one set of pure builders renders a
board for *any* deployment — the lab is just one profile.

Layout:

- :mod:`mqro.dashboards.profile` — the profile (identities + derivations from #351 ``short``/svc
  semantics).
- :mod:`mqro.dashboards.render` — the generator entry points (``render_*_board(profile)`` →
  deterministic Grafana JSON dict).
- :mod:`mqro.dashboards.boards` — the de-hardcoded board builders (a shared panel toolkit plus
  the per-board assemblies).

Stock ``ibmmq_*`` / ``node_*`` series and the app-SLA ``app_roundtrip_*`` signal are *external*
inputs (the stock exporter and the operator's own probes); only the ``cluster_*`` families this
repo's collectors emit are bound by the contract — enforced by ``tests.test_contract``.
"""

from __future__ import annotations

from mqro.dashboards.profile import DashboardProfile

__all__ = ["DashboardProfile"]
