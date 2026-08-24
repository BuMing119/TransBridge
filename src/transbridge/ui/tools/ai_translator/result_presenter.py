"""Result mapping and the single polish mutation boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PolishApplySummary:
    accepted: int
    rejected: int
    failed: int


@dataclass(frozen=True, slots=True)
class PolishReport:
    stats: dict
    report_path: str | None


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
            lines.append(f"润色: 成功 {polish.success_count}, 失败 {polish.failed_count}")
            details = getattr(polish, "details", None)
            failed = [detail for detail in details or () if not detail["success"]]
            if failed:
                lines.append(f"润色失败条目 ({len(failed)}):")
                lines.extend(f"  - {detail['key']}: {detail.get('error', '未知错误')[:50]}" for detail in failed[:5])
        return "\n".join(lines)

    def apply_direct(self, collection: object, entries: list, results: Mapping) -> PolishApplySummary:
        accepted = 0
        failed = 0
        for entry in entries:
            result = results.get(entry.id)
            if result and result.polished_translation and result.confidence > 0:
                self._commit_translation(collection, entry, result.polished_translation)
                accepted += 1
            else:
                failed += 1
        return PolishApplySummary(accepted, 0, failed)

    def apply_decisions(self, collection: object, entries: list, decisions: Mapping) -> PolishApplySummary:
        accepted = 0
        rejected = 0
        for entry in entries:
            decision = decisions.get(entry.id)
            if decision is not None:
                self._commit_translation(collection, entry, decision)
                accepted += 1
            elif entry.id in decisions:
                rejected += 1
        return PolishApplySummary(accepted, rejected, 0)

    @staticmethod
    def build_polish_report(
        results: Mapping,
        entries: list,
        summary: PolishApplySummary,
        *,
        polish_level: str,
        esp_path: str | None,
    ) -> PolishReport:
        failed = summary.failed or sum(1 for result in results.values() if result.confidence == 0.0)
        avg_confidence = sum(result.confidence for result in results.values()) / len(results) if results else 0
        stats = {
            "total": len(results),
            "accepted": summary.accepted,
            "rejected": summary.rejected,
            "failed": failed,
            "polish_level": polish_level,
            "avg_confidence": avg_confidence,
        }
        report_path = None
        try:
            from transbridge.ai_translator.post_processor.report_generator import ReportGenerator

            report_path = ReportGenerator(Path(esp_path).stem if esp_path else "unknown").generate_polish_report(
                results, entries, stats
            )
        except Exception:
            pass
        return PolishReport(stats, report_path)

    @staticmethod
    def _commit_translation(collection: object, entry: object, translation: str) -> None:
        updated = replace(entry, translation=translation)
        collection.add(updated, overwrite=True)
