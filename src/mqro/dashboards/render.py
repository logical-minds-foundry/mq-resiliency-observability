"""The metadata-driven render generator — a :class:`DashboardProfile` in, deterministic Grafana
dashboard JSON out. This is the portable replacement for the lab's hardcoded
``lab_*_dashboard()`` entry points: the same boards, but every identity threaded through the
profile instead of baked in as a constant.

The portable board set:

- :func:`render_qm_board` — the stock-only per-QM state board.
- :func:`render_messaging_board` — the stock-only messaging-flow board.
- :func:`render_cluster_board` — the Pacemaker/DRBD (SAN) cluster cockpit (``cluster_*`` PACEMAKER
  slice).
- :func:`render_nativeha_board` — the MQ Native HA + CRR cluster cockpit (``cluster_nha_*`` slice).
- :func:`render_rdqm_board` — the RDQM (DRBD + Pacemaker HA + cross-site DR) cluster cockpit
  (``cluster_rdqm_*`` slice).

:func:`render_all` renders the full set for a profile. :func:`dumps` serializes a board to
canonical JSON text (sorted-key-free, stable 2-space indent) so a re-render is byte-identical — the
determinism the tests assert and a reviewer can diff.

Pure functions throughout; no filesystem, no live stack.
"""

from __future__ import annotations

import json
from typing import Any

from mqro.dashboards.boards.cluster import render_cluster_board
from mqro.dashboards.boards.messaging import render_messaging_board
from mqro.dashboards.boards.nativeha import render_nativeha_board
from mqro.dashboards.boards.qm import render_qm_board
from mqro.dashboards.boards.rdqm import render_rdqm_board
from mqro.dashboards.profile import DashboardProfile

__all__ = [
    "DashboardProfile",
    "dumps",
    "render_all",
    "render_cluster_board",
    "render_messaging_board",
    "render_nativeha_board",
    "render_qm_board",
    "render_rdqm_board",
]


def dumps(board: dict[str, Any]) -> str:
    """Serialize a board dict to canonical JSON text (2-space indent, trailing newline). Stable
    across renders so the output is diff-friendly and byte-deterministic."""
    return json.dumps(board, indent=2) + "\n"


def render_all(profile: DashboardProfile) -> dict[str, dict[str, Any]]:
    """Render every board this slice ships for a profile, keyed by board uid. Deterministic:
    same profile → identical dicts."""
    boards = [
        render_qm_board(profile),
        render_messaging_board(profile),
        render_cluster_board(profile),
        render_nativeha_board(profile),
        render_rdqm_board(profile),
    ]
    return {board["uid"]: board for board in boards}
