"""持久化模块：项目、版本、翻译数据的 JSON 文件读写。

对应架构决策: ADR-006
"""

from .workspace import WorkspaceState
from .project import ProjectHandle
from .variant_store import VariantStore

from pathlib import Path
from src.transbridge.paratranz.config_manager import ParatranzConfig


def _data_dir() -> Path:
    return Path(ParatranzConfig.get_data_dir())


PERSISTENCE_ROOT = _data_dir() / "projects"


def workspace_path() -> Path:
    return _data_dir() / "workspace.json"
