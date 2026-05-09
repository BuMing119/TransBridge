"""current.json 翻译数据缓存管理。

对应: ADR-006 — current.json 结构
"""

import json
from pathlib import Path
from datetime import datetime
from typing import TYPE_CHECKING
from ._utils import atomic_write_json, validate_name

if TYPE_CHECKING:
    from src.transbridge.converter.translation_entry import TranslationEntry


class VariantStore:
    """管理 current.json —— 当前版本的译文、标签、标签库缓存。

    作为独立缓存层叠加在 TranslationEntryCollection 之上：
    - apply_to()    → 将缓存数据注入 TranslationEntry 列表
    - collect_from() → 从运行时状态收集数据到缓存
    - save/load      → JSON 文件读写
    """

    def __init__(self, path: Path):
        self._path = path
        self.translations: dict[str, str] = {}
        self.labels: dict[str, set[str]] = {}
        self.label_library: dict[str, dict] = {}
        self.dirty: bool = False

    # ── 工厂方法 ─────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path) -> "VariantStore":
        """从磁盘加载。文件不存在返回空 VariantStore。"""
        vs = cls(path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                vs.translations = data.get("translations", {})
                # labels 从 JSON array 还原为 set
                vs.labels = {
                    k: set(v) for k, v in data.get("labels", {}).items()
                }
                vs.label_library = data.get("label_library", {})
            except (json.JSONDecodeError, OSError):
                pass  # 返回空实例
        return vs

    def save(self) -> None:
        """原子写入 current.json。"""
        data = {
            "variant": self._path.parent.name,
            "updated": datetime.now().isoformat(),
            "translations": self.translations,
            "labels": {k: list(v) for k, v in self.labels.items()},
            "label_library": self.label_library,
        }
        atomic_write_json(self._path, data)
        self.dirty = False

    # ── 运行时桥接 ──────────────────────────────────────────────

    def apply_to(self, entries: list["TranslationEntry"]) -> int:
        """将缓存的 translation 注入 TranslationEntry 列表。

        返回: 更新的条目数
        """
        updated = 0
        for entry in entries:
            if not entry.id:
                continue
            trans = self.translations.get(entry.id)
            if trans is not None:
                entry.translation = trans
                updated += 1
        return updated

    def collect_from(
        self,
        entries: list["TranslationEntry"],
        entry_labels: dict[str, set[str]],
        label_library: dict[str, dict],
    ) -> None:
        """从运行时状态增量收集数据到缓存，设置 dirty=True。

        注意：translations 采用增量更新——仅更新传入 entries 中存在的条目，
        不在 entries 中的已有译文保留不变。这确保在筛选视图下调用不会丢失数据。
        labels 和 label_library 采用全量替换（与 UI 的 _entry_labels 保持一致）。
        """
        for e in entries:
            if not e.id:
                continue
            if e.translation:
                self.translations[e.id] = e.translation
        self.labels = {k: set(v) for k, v in entry_labels.items()}
        self.label_library = {k: dict(v) for k, v in label_library.items()}
        self.dirty = True

    # ── 快照操作 ─────────────────────────────────────────────────

    def save_snapshot(self, snapshot_dir: Path, name: str) -> Path:
        """另存为 snapshots/{timestamp}-{name}.json。"""
        name = validate_name(name)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{ts}-{name}.json"
        dest = snapshot_dir / filename

        data = {
            "variant": self._path.parent.name,
            "snapshot_name": name,
            "updated": datetime.now().isoformat(),
            "translations": self.translations,
            "labels": {k: list(v) for k, v in self.labels.items()},
            "label_library": self.label_library,
        }
        atomic_write_json(dest, data)
        return dest

    @classmethod
    def load_snapshot(cls, snapshot_path: Path) -> "VariantStore":
        """从快照文件加载为 VariantStore。"""
        return cls.load(snapshot_path)

    @staticmethod
    def list_snapshots(snapshot_dir: Path) -> list[dict]:
        """列出快照目录下所有快照。返回 [{name, path, updated}]。"""
        if not snapshot_dir.exists():
            return []
        result = []
        for f in sorted(snapshot_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                result.append({
                    "name": data.get("snapshot_name", f.stem),
                    "path": str(f),
                    "updated": data.get("updated", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return result

    @staticmethod
    def delete_snapshot(snapshot_path: Path) -> None:
        snapshot_path.unlink(missing_ok=True)
