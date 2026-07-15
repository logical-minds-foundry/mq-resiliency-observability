"""Shared DRBD-status parser — a contract-preserving extraction of the lab's
``clusterstate.parse_drbd``.

Stdlib-only (plain text tokenisation; no third-party imports), so this file deploys verbatim
onto the RDQM / Pacemaker nodes and is imported by both the RDQM collector (``rdqm.py``, #17)
and the Pacemaker cluster collector (``cluster.py``, #16). Both project the parsed shape into
the same ``cluster_drbd_*`` metric families, so the field names and semantics here are the
metric contract: change them and every downstream DRBD panel changes.
"""

from __future__ import annotations

from typing import Any


def parse_drbd(text: str) -> dict[str, Any]:
    """Parse `drbdsetup status --verbose --statistics` into
    {resource: {role,disk,conn,resync_pct,out_of_sync_bytes}}.

    Output is whitespace-indented `key:value` tokens::

        mqlun role:Primary suspended:no
          volume:0 minor:0 disk:UpToDate
          peer connection:Connected role:Secondary congested:no
            volume:0 replication:Established peer-disk:UpToDate ... done:73.2
                received:0 sent:65712 out-of-sync:0 ...

    `--json` is NOT used: some drbdsetup builds reject it (lab #272). A resource block starts
    at column 0 (`<name> role:...`); indented lines refine it. The connection state
    (Connected/StandAlone/...) drives the integrity light; an in-sync resource has no `done:`
    token, so resync reads 100%.
    """
    out: dict[str, dict[str, Any]] = {}
    cur: dict[str, Any] = {}
    for raw in text.splitlines():
        tokens = dict(t.split(":", 1) for t in raw.split() if ":" in t)
        if raw[:1] not in ("", " ", "\t") and "role" in tokens:
            cur = {
                "role": tokens["role"],
                "disk": "Unknown",
                "conn": "Unknown",
                "resync_pct": 100.0,
                "out_of_sync_bytes": 0,
            }
            out[raw.split()[0]] = cur
        if "disk" in tokens:  # local volume line (the peer line uses peer-disk:)
            cur["disk"] = tokens["disk"]
        if "connection" in tokens:  # peer line: Connected / StandAlone / ...
            cur["conn"] = tokens["connection"]
        if "done" in tokens:  # present only while resyncing
            cur["resync_pct"] = float(tokens["done"])
        if "out-of-sync" in tokens:
            cur["out_of_sync_bytes"] = int(tokens["out-of-sync"])
    return out
