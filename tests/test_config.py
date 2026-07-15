from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from mqro.config import Profile


def test_profile_carries_qm_and_textfile_dir():
    # both were lab-hardcoded (NHARAPP + an assumed textfile path); here they are inputs.
    prof = Profile(qm="QMNATIVE", textfile_dir=Path("/var/lib/node_exporter/textfile"))
    assert prof.qm == "QMNATIVE"
    assert prof.textfile_dir == Path("/var/lib/node_exporter/textfile")


def test_profile_is_frozen(tmp_path):
    prof = Profile(qm="QMNATIVE", textfile_dir=tmp_path)
    # a frozen dataclass raises FrozenInstanceError on mutation; the non-literal attr name
    # keeps this dynamic so neither ruff (B010) nor the type checkers reject the test itself.
    attr = "qm"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(prof, attr, "OTHER")
