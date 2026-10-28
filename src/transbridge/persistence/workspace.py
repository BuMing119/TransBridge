"""workspace.json 全局状态管理。

对应: ADR-006 — workspace.json 结构
"""

import json
from pathlib import Path

from ._utils import atomic_write_json


class WorkspaceState:
    """UI preferences and last-session filters; project catalogs live in the repository."""

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
                "filter_state": {},
            },
        }

    # ── 属性访问 ─────────────────────────────────────────────────

    @property
    def settings(self) -> dict:
        return self._data.setdefault("settings", {})

    @property
    def last_session(self) -> dict:
        return self._data.setdefault("last_session", {})
