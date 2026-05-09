"""workspace.json 全局状态管理。

对应: ADR-006 — workspace.json 结构
"""

import json
from pathlib import Path
from datetime import datetime
from ._utils import atomic_write_json


class WorkspaceState:
    """管理 workspace.json —— 项目列表、活跃引用、配置、会话状态。"""

    def __init__(self, path: Path):
        self._path = path
        self._data: dict = self._empty_template()

    # ── 工厂方法 ─────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path) -> "WorkspaceState":
        """从磁盘加载。文件不存在或损坏时返回空模板。"""
        ws = cls(path)
        if path.exists():
            try:
                ws._data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                ws._data = cls._empty_template()
        else:
            ws._data = cls._empty_template()
        return ws

    def save(self) -> None:
        atomic_write_json(self._path, self._data)

    # ── 模板 ─────────────────────────────────────────────────────

    @classmethod
    def _empty_template(cls) -> dict:
        return {
            "version": 1,
            "active_project": None,
            "projects": {},
            "settings": {
                "save_behavior": "prompt",
                "auto_save_interval_minutes": 5,
                "auto_save_on_edit": True,
                "write_back": {
                    "mode": "current_variant",
                    "last_output_dir": None,
                },
            },
            "last_session": {
                "project": None,
                "variant": None,
                "filter_state": {},
            },
        }

    # ── 属性访问 ─────────────────────────────────────────────────

    @property
    def projects(self) -> dict[str, str]:
        """{project_name: project_json_path}"""
        return self._data.get("projects", {})

    @projects.setter
    def projects(self, value: dict[str, str]) -> None:
        self._data["projects"] = value

    @property
    def active_project(self) -> str | None:
        return self._data.get("active_project")

    @active_project.setter
    def active_project(self, name: str | None) -> None:
        self._data["active_project"] = name

    @property
    def settings(self) -> dict:
        return self._data.setdefault("settings", {})

    @property
    def last_session(self) -> dict:
        return self._data.setdefault("last_session", {})

    # ── 便捷方法 ─────────────────────────────────────────────────

    def get_project_path(self, name: str) -> Path | None:
        """返回项目 project.json 的绝对路径。"""
        rel = self.projects.get(name)
        return Path(rel) if rel else None

    def add_project(self, name: str, project_json_path: Path) -> None:
        self.projects[name] = str(project_json_path)
        self.active_project = name

    def remove_project(self, name: str) -> None:
        self.projects.pop(name, None)
        if self.active_project == name:
            remaining = list(self.projects.keys())
            self.active_project = remaining[0] if remaining else None
