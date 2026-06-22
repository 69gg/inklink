from pathlib import Path

import pytest


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "novel"
