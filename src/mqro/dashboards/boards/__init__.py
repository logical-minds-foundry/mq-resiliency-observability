"""De-hardcoded board builders: a shared panel toolkit plus the per-board assemblies.

- :mod:`~mqro.dashboards.boards.primitives` — the Grafana panel toolkit (stat/timeseries/table/
  logs/state-timeline builders, the badge chip, the colour mappings, the row-key normalizer).
  Ported from the lab's ``clusterboard`` primitives; the only change is that datasource UIDs are
  always passed in, never assumed.
- :mod:`~mqro.dashboards.boards.qm` — the stock-only per-QM board (queries ``ibmmq_*`` only).
- :mod:`~mqro.dashboards.boards.messaging` — the stock-only messaging-flow board (``ibmmq_*`` plus
  the external ``app_roundtrip_*`` app-SLA signal).
- :mod:`~mqro.dashboards.boards.cluster` — the Pacemaker/DRBD (SAN) cluster cockpit, riding the
  ``cluster_*`` PACEMAKER contract slice.
- :mod:`~mqro.dashboards.boards.nativeha` — the MQ Native HA + CRR cluster cockpit, riding the
  ``cluster_nha_*`` NATIVEHA contract slice.
- :mod:`~mqro.dashboards.boards.rdqm` — the RDQM (DRBD + Pacemaker HA + cross-site DR) cluster
  cockpit, riding the ``cluster_rdqm_*`` RDQM contract slice.
"""

from __future__ import annotations
