"""持久化模块：项目、版本、翻译数据的 JSON 文件读写。

对应架构决策: ADR-006
"""

from pathlib import Path

from transbridge.paratranz.config_manager import ParatranzConfig

from .project import ProjectHandle as ProjectHandle
from .variant_store import VariantStore as VariantStore
from .workspace import WorkspaceState as WorkspaceState


def _data_dir() -> Path:
    return Path(ParatranzConfig.get_data_dir())


PERSISTENCE_ROOT = _data_dir() / "projects"


def workspace_path() -> Path:
    return _data_dir() / "workspace.json"
