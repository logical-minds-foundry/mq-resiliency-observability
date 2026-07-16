"""Shared, stdlib-only building blocks every mqro collector reuses.

Extracted from the lab's ``nativehastate.py`` so each collector renders and writes the
same way:

- :func:`metric_line`     — format one Prometheus sample line (``name{k="v",...} value``).
- :func:`probe`           — run a command bounded by a timeout; stdout on success, else
  ``None`` (which callers treat as STALE — no fresh sample this tick).
- :func:`last_write_lines`— stamp the shared ``cluster_state_last_write_timestamp``
  freshness family, one sample per source that produced a fresh reading.
- :func:`write_textfile`  — write a node_exporter textfile atomically (temp file + rename)
  so a reader never sees a half-written render.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

# The freshness family every collector stamps. A source that produced a fresh reading this
# tick gets one sample; a source that went STALE (its probe returned None) simply does not
# appear, so its timestamp never advances and staleness alerting can catch the stall.
_LAST_WRITE_METRIC = "cluster_state_last_write_timestamp"


def metric_line(name: str, labels: Mapping[str, object], value: object) -> str:
    """Format one Prometheus sample line: ``name{k="v",...} value``."""
    rendered = ",".join(f'{k}="{v}"' for k, v in labels.items())
    return f"{name}{{{rendered}}} {value}"


def probe(cmd: list[str], timeout: int, *, ignore_rc: bool = False) -> str | None:
    """Run cmd bounded; return stdout on success, None on timeout/nonzero/OSError (-> STALE).

    ``ignore_rc=True`` returns stdout regardless of exit code — for tools like
    ``systemctl is-active`` that report a valid state ("inactive") with a non-zero exit, where
    non-zero means "down", not "the probe failed". A timeout/OSError still yields ``None`` (no
    reading at all -> STALE), so a genuinely unreachable probe is never mistaken for a down reading.
    """
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except (subprocess.TimeoutExpired, OSError):
        return None
    if ignore_rc:
        return cp.stdout
    return cp.stdout if cp.returncode == 0 else None


def last_write_lines(node: str, fresh_sources: tuple[str, ...], now: int) -> list[str]:
    """One ``cluster_state_last_write_timestamp`` sample per fresh source (empty -> no lines)."""
    return [
        metric_line(_LAST_WRITE_METRIC, {"node": node, "source": source}, now)
        for source in fresh_sources
    ]


def write_textfile(path: Path, body: str) -> None:
    """Write body to path atomically: fill a sibling ``.tmp`` file, then rename over target.

    node_exporter's textfile collector may read at any instant; the rename is atomic on a
    POSIX filesystem, so a scrape sees either the previous render or the new one in full,
    never a partial write.
    """
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(body)
    tmp.replace(path)
