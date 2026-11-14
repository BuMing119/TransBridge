# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Apply automatic equivalence judgments without changing evidence or human choices."""

from dataclasses import replace

from .models import ConflictStatus


def judged_reduction(result, judge, *, baseline, reconciler, cancellation=None):
    if judge is None:
        return replace(result, diagnostics=(*result.diagnostics, "CONFLICT_JUDGMENT_DISABLED"))
    judgments = judge.judge(result.conflicts, result.evidence, cancellation=cancellation)
    by_id = {item.conflict_group_id: item for item in judgments}
    conflicts = []
    equivalent = uncertain = 0
    completed = not_run = failed = requests = 0
    for group in result.conflicts:
        judgment = by_id.get(group.conflict_group_id)
        if judgment is not None:
            requests += judgment.request_count
            completed += judgment.execution_status == "completed"
            not_run += judgment.execution_status == "not_run"
            failed += judgment.execution_status == "failed"
            if judgment.execution_status != "completed":
                label = "未执行冲突判断" if judgment.execution_status == "not_run" else "冲突判断失败"
                conflicts.append(
                    replace(group, notes=f"{label}（模型请求 {judgment.request_count} 次）：{judgment.rationale}")
                )
                continue
        valid = (
            judgment is not None
            and judgment.classification in {"equivalent", "conflict", "uncertain"}
            and bool(judgment.rationale.strip())
        )
        if valid and judgment.classification == "equivalent":
            valid = judgment.canonical_translation in {item.normalized_translation for item in group.variants}
        if not valid:
            uncertain += 1
            conflicts.append(replace(group, notes="LLM 未返回有效判断，保留待人工处理。"))
        elif judgment.classification == "equivalent":
            equivalent += 1
            conflicts.append(
                replace(
                    group,
                    status=ConflictStatus.EQUIVALENT,
                    recommended_translation=judgment.canonical_translation,
                    notes=f"LLM 判断：同一术语的正常表达差异。\n{judgment.rationale}",
                )
            )
        else:
            uncertain += judgment.classification == "uncertain"
            label = "无法确定" if judgment.classification == "uncertain" else "存在译名冲突"
            conflicts.append(replace(group, notes=f"LLM 判断：{label}。\n{judgment.rationale}"))
    conflicts = tuple(conflicts)
    reconciliation = reconciler.reconcile(result.candidates, baseline, conflicts)
    diagnostics = (
        tuple(item for item in result.diagnostics if item not in result.reconciliation.diagnostics)
        + reconciliation.diagnostics
    )
    return replace(
        result,
        conflicts=conflicts,
        reconciliation=reconciliation,
        diagnostics=(
            *diagnostics,
            f"CONFLICT_JUDGMENT_COMPLETED:{completed}",
            f"CONFLICT_JUDGMENT_NOT_RUN:{not_run}",
            f"CONFLICT_JUDGMENT_FAILED:{failed}",
            f"CONFLICT_JUDGMENT_REQUESTS:{requests}",
            f"CONFLICT_JUDGMENT_EQUIVALENT:{equivalent}",
            f"CONFLICT_JUDGMENT_UNCERTAIN:{uncertain}",
        ),
    )
