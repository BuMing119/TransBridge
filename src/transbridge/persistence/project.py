"""project.json 项目配置管理。

对应: ADR-006 — project.json 结构
"""

from datetime import datetime
import json
from pathlib import Path

from ._utils import atomic_write_json, validate_name


class ProjectHandle:
    """管理 project.json —— 项目名称、源文件列表、版本列表。"""

    def __init__(self, path: Path):
        self._path = path
        self._data: dict = {}

    # ── 工厂方法 ─────────────────────────────────────────────────

    @classmethod
    def create(cls, base_dir: Path, name: str, sources: list[dict] | None = None) -> "ProjectHandle":
        """创建新项目目录和 project.json。"""
        name = validate_name(name)
        proj_dir = base_dir / name
        proj_dir.mkdir(parents=True, exist_ok=True)
        ph = cls(proj_dir / "project.json")
        ph._data = {
            "name": name,
            "created": datetime.now().isoformat(),
            "sources": sources or [],
            "variants": [],
            "active_variant": None,
            "esp_key_format": True,
        }
        ph.save()
        return ph

    @classmethod
    def load(cls, path: Path) -> "ProjectHandle":
        """从磁盘加载。文件不存在或损坏返回空 ProjectHandle。"""
        ph = cls(path)
        if path.exists():
            try:
                ph._data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return ph

    def save(self) -> None:
        atomic_write_json(self._path, self._data)

    # ── 属性 ─────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._data.get("name", "")

    @property
    def sources(self) -> list[dict]:
        return self._data.get("sources", [])

    @sources.setter
    def sources(self, value: list[dict]) -> None:
        self._data["sources"] = value

    @property
    def variants(self) -> list[dict]:
        return self._data.get("variants", [])

    @property
    def active_variant(self) -> str | None:
        return self._data.get("active_variant")

    @active_variant.setter
    def active_variant(self, name: str | None) -> None:
        self._data["active_variant"] = name

    @property
    def project_dir(self) -> Path:
        return self._path.parent

    @property
    def config_path(self) -> Path:
        return self._path

    # ── 版本操作 ─────────────────────────────────────────────────

    def add_variant(self, name: str, copied_from: str | None = None) -> None:
        """追加版本到 variants 列表（不创建目录，由调用方负责）。"""
        name = validate_name(name)
        for v in self.variants:
            if v["name"] == name:
                raise ValueError(f"版本名已存在: {name}")
        self.variants.append({
            "name": name,
            "created": datetime.now().isoformat(),
            "copied_from": copied_from,
        })

    def remove_variant(self, name: str) -> None:
        """从列表移除版本（不删除磁盘文件）。"""
        self._data["variants"] = [v for v in self.variants if v["name"] != name]
        if self.active_variant == name:
            remaining = [v["name"] for v in self.variants]
            self.active_variant = remaining[0] if remaining else None

    def has_variant(self, name: str) -> bool:
        return any(v["name"] == name for v in self.variants)

    def variant_dir(self, variant_name: str) -> Path:
        """返回 {project_dir}/{variant_name}/ 路径。"""
        return self.project_dir / variant_name

    # ── 源文件操作 ───────────────────────────────────────────────

    def add_source(self, key: str, source_type: str, path: str) -> None:
        """追加源文件到列表。"""
        self._data.setdefault("sources", []).append({
            "key": key,
            "type": source_type,
            "path": path,
        })

    def remove_source(self, key: str) -> None:
        self._data["sources"] = [s for s in self.sources if s.get("key") != key]
