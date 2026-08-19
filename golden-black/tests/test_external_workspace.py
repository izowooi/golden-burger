from pathlib import Path

import pytest

from scripts.verify_external_workspace import inspect_workspace


def test_internal_workspace_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="/Volumes"):
        inspect_workspace(tmp_path)
