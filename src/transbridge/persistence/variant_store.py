"""current.json 翻译数据缓存管理。

对应: ADR-006 — current.json 结构
"""

from datetime import datetime
import json
from pathlib import Path
from typing import TYPE_CHECKING
import warnings

from ._utils import atomic_write_json, validate_name
from .v2.variant import plan_legacy_variant_projection

if TYPE_CHECKING:
    from transbridge.converter.translation_entry import TranslationEntry


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
        self.entry_states: dict[str, dict] = {}
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
                vs.labels = {k: set(v) for k, v in data.get("labels", {}).items()}
                vs.label_library = data.get("label_library", {})
                vs.entry_states = data.get("entry_states", {})
            except (json.JSONDecodeError, OSError):
                pass  # 返回空实例
        return vs

    def save(self) -> None:
        """原子写入 current.json。"""
        data = {
            "variant": self._path.parent.name,
            "updated": datetime.now().isoformat(),
            "translations": self.translations,
            "labels": {k: sorted(v) for k, v in self.labels.items()},
            "label_library": self.label_library,
            "entry_states": self.entry_states,
        }
        atomic_write_json(self._path, data)
        self.dirty = False

    # ── 运行时桥接 ──────────────────────────────────────────────

    def apply_to(
        self,
        entries: list["TranslationEntry"],
        *,
        source_baseline: list["TranslationEntry"] | None = None,
    ) -> int:
        """将兼容缓存投影到旧 TranslationEntry 列表。

        ``source_baseline`` 是完整 replace 所必需的迁移参数。旧调用方未提供
        baseline 时只能覆盖快照显式包含的条目；此 facade 不会把进程当前状态
        冒充来源基线。权威的 stage/revision/provenance/labels 恢复由 V2 aggregate
        materializer 完成。
        """
        stages = {
            key: int(value["stage"])
            for key, value in self.entry_states.items()
            if isinstance(value, dict) and "stage" in value
        }
        projections, complete = plan_legacy_variant_projection(
            entries,
            self.translations,
            stages,
            source_baseline=source_baseline,
        )
        if not complete:
            warnings.warn(
                "VariantStore.apply_to() without a complete source_baseline is a lossy "
                "compatibility path; migrate the caller to the V2 materializer.",
                DeprecationWarning,
                stacklevel=2,
            )
        for projection in projections:
            projection.entry.translation = projection.translation
            projection.entry.stage = projection.stage
        return len(projections)

    def collect_from(
        self,
        entries: list["TranslationEntry"],
        entry_labels: dict[str, set[str]],
        label_library: dict[str, dict],
    ) -> None:
        """从完整 aggregate 投影全量收集，空值具有显式状态语义。

        传入筛选后的视图将产生筛选后的完整快照；调用方迁移到 V2 时必须传
        aggregate 而不是 UI 过滤结果。
        """
        included = [entry for entry in entries if entry.id]
        self.translations = {entry.id: entry.translation for entry in included}
        self.entry_states = {
            entry.id: {
                "stage": entry.stage,
                "revision": entry.revision.value,
                "provenance": [item.to_dict() for item in entry.provenance],
            }
            for entry in included
        }
        self.labels = {entry.id: set(entry_labels.get(entry.id, ())) for entry in included}
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
            "labels": {k: sorted(v) for k, v in self.labels.items()},
            "label_library": self.label_library,
            "entry_states": self.entry_states,
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
