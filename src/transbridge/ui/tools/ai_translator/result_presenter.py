"""Result mapping and the single polish mutation boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from uuid import uuid4

from transbridge.application.translation import ReportSnapshot, build_polish_report_snapshot
from transbridge.application.translation.entry_alias import ai_entry_id


@dataclass(frozen=True, slots=True)
class PolishApplySummary:
    accepted: int
    rejected: int
    failed: int
    accepted_entry_ids: tuple[str, ...] = ()
    rejected_entry_ids: tuple[str, ...] = ()
    failed_entry_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PolishReport:
    snapshot: ReportSnapshot


class ResultPresenter:
    """Maps worker results; preview remains read-only until ``apply_*``."""

    @staticmethod
    def mixed_summary(result: Mapping[str, object]) -> str:
        translate = result.get("translate")
        polish = result.get("polish")
        lines = ["混合执行完成:"]
        if translate:
            lines.append(f"翻译: 成功 {translate.success_count}, 失败 {translate.failed_count}")
        if polish:
            pending_count = int(getattr(polish, "pending_count", 0))
            lines.append(
                f"润色: 成功 {polish.success_count}, 失败 {polish.failed_count}"
                + (f"，待审 {pending_count}" if pending_count else "")
            )
            details = getattr(polish, "details", None)
            failed = [detail for detail in details or () if detail.get("verdict") in {"error", "failed", "reject"}]
            if failed:
                lines.append(f"润色失败条目 ({len(failed)}):")
                lines.extend(f"  - {detail['key']}: {detail.get('error', '未知错误')[:50]}" for detail in failed[:5])
        return "\n".join(lines)

    def apply_mixed_polish(self, collection: object, entries: list, result: Mapping[str, object]) -> bool:
        polish = result.get("polish")
        if polish is None:
            return False
        self.apply_direct(collection, entries, polish.candidates)
        return True

    def apply_direct(self, collection: object, entries: list, results: Mapping) -> PolishApplySummary:
        accepted_ids: list[str] = []
        rejected_ids: list[str] = []
        failed_ids: list[str] = []
        for entry in entries:
            result = results.get(ai_entry_id(entry))
            accepted_result = result and bool(getattr(result, "accepted", result.confidence > 0))
            if accepted_result and result.polished_translation:
                self._commit_translation(
                    collection, entry, result.polished_translation, getattr(result, "target_stage", None)
                )
                accepted_ids.append(ai_entry_id(entry))
            elif result and result.confidence > 0:
                rejected_ids.append(ai_entry_id(entry))
            else:
                failed_ids.append(ai_entry_id(entry))
        return PolishApplySummary(
            len(accepted_ids),
            len(rejected_ids),
            len(failed_ids),
            tuple(accepted_ids),
            tuple(rejected_ids),
            tuple(failed_ids),
        )

    def apply_decisions(
        self,
        collection: object,
        entries: list,
        decisions: Mapping,
        *,
        results: Mapping | None = None,
    ) -> PolishApplySummary:
        accepted_ids: list[str] = []
        rejected_ids: list[str] = []
        failed_ids: list[str] = []
        for entry in entries:
            decision = decisions.get(ai_entry_id(entry))
            if decision is not None:
                result = results.get(ai_entry_id(entry)) if results is not None else None
                self._commit_translation(collection, entry, decision, getattr(result, "target_stage", None))
                accepted_ids.append(ai_entry_id(entry))
            elif ai_entry_id(entry) in decisions:
                result = results.get(ai_entry_id(entry)) if results is not None else None
                if results is not None and (result is None or getattr(result, "confidence", 0.0) <= 0.0):
                    failed_ids.append(ai_entry_id(entry))
                else:
                    rejected_ids.append(ai_entry_id(entry))
            else:
                failed_ids.append(ai_entry_id(entry))
        return PolishApplySummary(
            len(accepted_ids),
            len(rejected_ids),
            len(failed_ids),
            tuple(accepted_ids),
            tuple(rejected_ids),
            tuple(failed_ids),
        )

    @staticmethod
    def build_polish_report(
        results: Mapping,
        entries: list,
        summary: PolishApplySummary,
        *,
        polish_level: str,
        esp_path: str | None,
        run_spec: object | None = None,
    ) -> PolishReport:
        run_id = str(getattr(run_spec, "run_id", "") or f"polish-{uuid4().hex}")
        snapshot = build_polish_report_snapshot(
            results,
            entries,
            accepted_entry_ids=summary.accepted_entry_ids,
            rejected_entry_ids=summary.rejected_entry_ids,
            failed_entry_ids=summary.failed_entry_ids,
            run_id=run_id,
            polish_level=polish_level,
            run_spec_summary=_run_spec_summary(run_spec),
        )
        return PolishReport(snapshot)

    @staticmethod
    def _commit_translation(
        collection: object, entry: object, translation: str, target_stage: int | None = None
    ) -> None:
        updated = replace(entry, translation=translation, stage=entry.stage if target_stage is None else target_stage)
        collection.add(updated, overwrite=True)


def _run_spec_summary(run_spec: object | None) -> dict[str, object]:
    if run_spec is None:
        return {}
    profile = getattr(run_spec, "execution_profile", None)
    return {
        "run_mode": str(getattr(getattr(run_spec, "mode", None), "value", getattr(run_spec, "mode", "polish"))),
        "input_fingerprint": str(getattr(run_spec, "input_fingerprint", "")),
        "config_digest": str(getattr(run_spec, "config_digest", "")),
        "execution_profile": {
            "stages": list(getattr(profile, "stages", ())),
            "summary": str(getattr(profile, "summary", "")),
            "digest": str(getattr(profile, "digest", "")),
        },
    }
