from pathlib import Path
from shutil import copyfile

import pytest


@pytest.fixture
def skyrim_plugin_copy(request, tmp_path):
    """Give one write test its own plugin and three loose English string tables."""
    backup = request.config.rootpath / "data/test-fixtures/skyrim-original/Data"
    if not backup.exists():
        pytest.skip(f"Local Skyrim plugin backups are unavailable: {backup}")
    name = request.param
    assert Path(name).name == name and Path(name).suffix.lower() in {".esm", ".esl"}
    plugin = Path(name)
    data = tmp_path / "Data"
    (data / "Strings").mkdir(parents=True)
    copyfile(backup / plugin, data / plugin)
    for suffix in ("strings", "dlstrings", "ilstrings"):
        table = Path("Strings") / f"{plugin.stem.lower()}_english.{suffix}"
        copyfile(backup / table, data / table)
    return data / plugin
