"""Runtime configuration for mqro collectors.

In the lab both the queue-manager name (a hardcoded ``NHARAPP``) and the node_exporter
textfile path (an assumed ``/var/lib/node_exporter/textfile/...``) were baked into the
collector. Extracted here, both become explicit inputs so a collector can run against any
queue manager and write into any textfile-collector directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class Profile:
    """What a collector needs to know: which queue manager, and where to write.

    Attributes:
        qm: The queue-manager name passed to ``dspmq`` (was the hardcoded ``NHARAPP``).
        textfile_dir: The node_exporter textfile-collector directory the ``.prom`` file
            lands in (was an assumed absolute path in the lab).
    """

    qm: str
    textfile_dir: Path
