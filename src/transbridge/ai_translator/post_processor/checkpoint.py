"""后处理断点续传数据类。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import PostProcessIssue
    from .llm_arbiter import ArbiterDecision
    from .llm_refiner import RefineResult
    from .polisher import PolishResult


@dataclass
class PostProcessCheckpoint:
    """后处理进度断点。

    存储路径: data/ai_translator/{esp_stem}/{esp_stem}_post_process.json
    """

    esp_stem: str
    completed_batches: dict[str, list[list[str]]] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    refine_results: dict[str, dict] = field(default_factory=dict)
    polish_results: dict[str, dict] = field(default_factory=dict)
    decisions: dict[str, dict] = field(default_factory=dict)

    def is_batch_completed(self, phase: str, entry_ids: list[str]) -> bool:
        """检查某批次是否已完成。"""
        fp = sorted(entry_ids)
        batches = self.completed_batches.get(phase, [])
        return fp in batches

    def mark_batch_completed(self, phase: str, entry_ids: list[str]) -> None:
        """标记某批次为已完成。"""
        if phase not in self.completed_batches:
            self.completed_batches[phase] = []
        fp = sorted(entry_ids)
        if fp not in self.completed_batches[phase]:
            self.completed_batches[phase].append(fp)

    def save(self, esp_path: str | Path) -> Path:
        """保存断点到文件。"""
        path = self._get_path(esp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "esp_stem": self.esp_stem,
                    "completed_batches": self.completed_batches,
                    "issues": self.issues,
                    "refine_results": self.refine_results,
                    "polish_results": self.polish_results,
                    "decisions": self.decisions,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        return path

    @classmethod
    def load(cls, esp_path: str | Path) -> PostProcessCheckpoint | None:
        """从文件加载断点。"""
        path = cls._get_path(esp_path)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return cls(
                esp_stem=data.get("esp_stem", ""),
                completed_batches=data.get("completed_batches", {}),
                issues=data.get("issues", []),
                refine_results=data.get("refine_results", {}),
                polish_results=data.get("polish_results", {}),
                decisions=data.get("decisions", {}),
            )
        except Exception:
            return None

    def delete(self, esp_path: str | Path) -> None:
        """删除断点文件。"""
        path = self._get_path(esp_path)
        if path.exists():
            path.unlink()

    @staticmethod
    def _get_path(esp_path: str | Path) -> Path:
        from ...paratranz.config_manager import ParatranzConfig

        esp_path = Path(esp_path)
        stem = esp_path.stem
        data_dir = Path(ParatranzConfig.get_data_dir())
        return data_dir / "ai_translator" / stem / f"{stem}_post_process.json"

    # ── 序列化辅助方法 ──────────────────────────────────────────────────────

    @staticmethod
    def issue_to_dict(issue: PostProcessIssue) -> dict:
        return asdict(issue)

    @staticmethod
    def issue_from_dict(data: dict) -> PostProcessIssue:
        from .base import PostProcessIssue

        return PostProcessIssue(**data)

    @staticmethod
    def refine_result_to_dict(result: RefineResult) -> dict:
        return asdict(result)

    @staticmethod
    def refine_result_from_dict(data: dict) -> RefineResult:
        from .llm_refiner import FixApplied, RefineResult

        fixes = [FixApplied(**f) for f in data.get("fixes_applied", [])]
        return RefineResult(
            entry_id=data["entry_id"],
            original_translation=data.get("original_translation", ""),
            refined_translation=data.get("refined_translation", ""),
            fixes_applied=fixes,
            confidence=data.get("confidence", 0.0),
            needs_arbitration=data.get("needs_arbitration", False),
            note=data.get("note", ""),
        )

    @staticmethod
    def polish_result_to_dict(result: PolishResult) -> dict:
        return asdict(result)

    @staticmethod
    def polish_result_from_dict(data: dict) -> PolishResult:
        from .polisher import PolishResult

        return PolishResult(
            entry_id=data["entry_id"],
            original_translation=data.get("original_translation", ""),
            polished_translation=data.get("polished_translation", ""),
            changes=data.get("changes", []),
            confidence=data.get("confidence", 0.0),
            needs_arbitration=data.get("needs_arbitration", False),
            note=data.get("note", ""),
        )

    @staticmethod
    def decision_to_dict(decision: ArbiterDecision) -> dict:
        return asdict(decision)

    @staticmethod
    def decision_from_dict(data: dict) -> ArbiterDecision:
        from .llm_arbiter import ArbiterDecision

        return ArbiterDecision(
            entry_id=data["entry_id"],
            verdict=data["verdict"],
            reason=data.get("reason", ""),
            confidence=data.get("confidence", 0.0),
            suggested_action=data.get("suggested_action", ""),
            alternatives=data.get("alternatives", []),
        )
