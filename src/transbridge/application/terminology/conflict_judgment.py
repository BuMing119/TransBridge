# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Evidence coverage and validated judgments for apparent terminology conflicts."""

from dataclasses import dataclass
from typing import Protocol

from .conflict_usage import conflict_usage


class ConflictJudgePort(Protocol):
    def judge(self, conflicts, evidence, *, cancellation=None) -> tuple["ConflictJudgment", ...]: ...


class ConflictJudgmentCancelled(RuntimeError):
    """A judgment run stopped without applying partial model decisions."""


@dataclass(frozen=True, slots=True)
class ConflictJudgment:
    conflict_group_id: str
    classification: str
    rationale: str
    canonical_translation: str | None = None
    execution_status: str = "completed"
    request_count: int = 1

    def __post_init__(self):
        if self.execution_status not in {"completed", "not_run", "failed"} or self.request_count < 0:
            raise ValueError("invalid judgment execution status")
        if not isinstance(self.conflict_group_id, str) or not self.conflict_group_id.strip():
            raise ValueError("conflict judgment requires a group ID")
        if self.classification not in {"equivalent", "conflict", "uncertain"}:
            raise ValueError("unknown conflict judgment classification")
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise ValueError("conflict judgment requires a rationale")
        if self.classification == "equivalent":
            if not isinstance(self.canonical_translation, str) or not self.canonical_translation.strip():
                raise ValueError("equivalent terms require an existing canonical translation")
        elif self.canonical_translation is not None:
            raise ValueError("unresolved conflicts cannot select a canonical translation")


def judgment_payload(conflict, evidence, *, majority_samples=3):
    """Keep every minority record and explicitly identify sampled majority evidence."""
    by_id = evidence if isinstance(evidence, dict) else {item.evidence_id: item for item in evidence}
    if any(identity not in by_id for variant in conflict.variants for identity in variant.evidence_ids):
        raise ValueError("冲突来源证据缺失，请重新提取后判断")
    variants = []
    for usage in conflict_usage(conflict, by_id):
        selected = usage.evidence if usage.minority else usage.evidence[:majority_samples]
        if not selected:
            raise ValueError("候选译名缺少可核对的双语词条")
        variants.append({
            "translation": usage.translation,
            "unique_record_count": usage.count,
            "coverage": "complete" if len(selected) == usage.count else "majority_sample",
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "original": item.original,
                    "translation": item.translation,
                    "context": item.context,
                    "plugin": item.plugin_scope,
                }
                for item in selected
            ],
        })
    return {
        "conflict_group_id": conflict.conflict_group_id,
        "original": conflict.normalized_original,
        "variants": variants,
    }


def validate_judgment(data, conflict):
    if not isinstance(data, dict):
        raise ValueError("冲突判断返回格式无效")
    if set(data) != {"conflict_group_id", "classification", "rationale", "canonical_translation"}:
        raise ValueError("冲突判断字段缺失或包含未知字段")
    judgment = ConflictJudgment(**data)
    if judgment.conflict_group_id != conflict.conflict_group_id:
        raise ValueError("冲突判断引用了其他术语")
    if judgment.classification == "equivalent" and judgment.canonical_translation not in {
        variant.normalized_translation for variant in conflict.variants
    }:
        raise ValueError("模型选择的术语译名不在已有候选中")
    return judgment


def check_judgment_cancelled(cancellation):
    if cancellation is not None and cancellation.is_cancelled:
        raise ConflictJudgmentCancelled("术语冲突判断已取消")
