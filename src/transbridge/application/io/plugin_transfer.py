"""Apply a frozen project's proved translations to a separately selected plugin."""

from dataclasses import dataclass, replace

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    DomainError,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)

from .contracts import FormatId, ParseRequest, SourceDescriptor
from .legacy_adapters import SsePluginAdapter
from .operation_write import (
    HydratedWriteDraft,
    HydratedWritePreflight,
    HydratedWritePreflightService,
    HydratedWriteWorkload,
    WritePreflightCheck,
    _translation_entries,
)
from .plugin_transfer_matching import PluginTransferIssue, PluginTransferReport, match_plugin_translations
from .plugin_transfer_snapshot import (
    TransferCommitGuard,
    TransferFileLease,
    canonical,
    freeze_template,
    protect_project_source,
    target_entries_from_snapshot,
    verify_leases,
)
from .plugin_write import _snapshot_localized_sources, plugin_artifact_paths
from .publish import BackupPolicy


@dataclass(frozen=True, slots=True)
class PluginTransferDraft:
    write: HydratedWriteDraft
    plugin_path: str


@dataclass(frozen=True, slots=True)
class PluginTransferPreflight:
    draft: PluginTransferDraft
    write_preflight: HydratedWritePreflight | None
    checks: tuple[WritePreflightCheck, ...]
    report: PluginTransferReport
    leases: tuple[TransferFileLease, ...] = ()

    @property
    def ready(self) -> bool:
        return (
            self.write_preflight is not None
            and self.write_preflight.ready
            and all(item.passed or item.warning for item in self.checks)
        )


class PluginTransferPreflightService:
    def __init__(self, write_preflights: HydratedWritePreflightService | None = None) -> None:
        self._project_preflights = write_preflights or HydratedWritePreflightService()

    def preflight(self, draft: PluginTransferDraft) -> PluginTransferPreflight:
        report = PluginTransferReport(source_count=len(draft.write.entries))
        checks: list[WritePreflightCheck] = []
        try:
            if draft.write.format_id is not FormatId.PLUGIN_SSE:
                raise ValueError("套用译文需要项目的 ESP/ESM 来源快照。")
            entries = _translation_entries(draft.write.entries)
            projection = self._project_preflights.freeze_projection(draft.write)
            projection_metadata = ()
            projection_issues = ()
            if projection is not None:
                entries, diagnostics = projection.project_entries(entries)
                projection_metadata = projection.metadata
                projection_issues = tuple(
                    PluginTransferIssue("TRANSFER_TERMINOLOGY_WARNING", "", "", str(item.message))
                    for item in diagnostics
                )
            options = dict(draft.write.options)
            options.setdefault(
                "language", dict(draft.write.source_snapshot.metadata).get("localized_language", "english")
            )
            options["skip_empty"] = False
            options["discover_sibling_strings"] = True
            parsed = SsePluginAdapter().parse(
                ParseRequest(
                    SourceDescriptor(draft.plugin_path),
                    draft.write.context,
                    FormatId.PLUGIN_SSE,
                    options=tuple(options.items()),
                )
            )
            acceptable = parsed.outcome is OperationOutcome.COMPLETED or (
                parsed.outcome is OperationOutcome.PARTIAL
                and all(item.code == "SOURCE_LOCATOR_CONFLICT" for item in parsed.diagnostics)
            )
            if not acceptable or parsed.source_snapshot is None:
                raise ValueError("目标插件无法完整解析：" + "；".join(item.message for item in parsed.diagnostics))
            if dict(parsed.source_snapshot.metadata).get(
                "localized_lookup_detected"
            ) and not _snapshot_localized_sources(parsed.source_snapshot):
                raise ValueError("目标使用本地化字符串，但缺少可写入的独立 Strings 文件。")
            leases = freeze_template(parsed.source_snapshot)
            selected, report = match_plugin_translations(
                entries,
                draft.write.source_snapshot,
                target_entries_from_snapshot(parsed.source_snapshot),
                parsed.source_snapshot,
            )
            report = replace(report, issues=(*projection_issues, *report.issues))
            checks.append(WritePreflightCheck("TRANSFER_MATCH_SUMMARY", bool(selected), report.summary))
            checks.extend(
                WritePreflightCheck(
                    f"TRANSFER_ITEM_{index}_{issue.code}",
                    False,
                    f"{issue.key}（原文 {issue.original[:120]!r}）：{issue.message}" if issue.key else issue.message,
                    warning=True,
                )
                for index, issue in enumerate(report.issues, 1)
            )
            if not selected:
                checks.append(
                    WritePreflightCheck("TRANSFER_EMPTY", False, "没有可安全套用的有效译文，未生成无变化输出。")
                )
                return PluginTransferPreflight(draft, None, tuple(checks), report, leases)
            context_metadata = dict(draft.write.context.metadata)
            context_metadata.update(projection_metadata)
            child = replace(
                draft.write,
                source_snapshot=parsed.source_snapshot,
                entries=selected,
                context=replace(draft.write.context, metadata=tuple(sorted(context_metadata.items()))),
                backup_policy=BackupPolicy.REQUIRED_IF_EXISTS,
                options=tuple(options.items()),
            )
            checked = HydratedWritePreflightService().preflight(child)
            checked = replace(checked, projection_metadata=projection_metadata)
            protect_project_source(
                draft.write.source_snapshot,
                parsed.source_snapshot,
                (path for path, _fingerprint in checked.artifact_fingerprints),
                child.conflict_policy,
            )
            output_requests = (
                {
                    canonical(path): path
                    for path in plugin_artifact_paths(child.source_snapshot, child.target_path, child.options)
                }
                if child.target_path.strip()
                else {}
            )
            leases += tuple(
                TransferFileLease(canonical(path), fingerprint, False, output_requests[canonical(path)])
                for path, fingerprint in checked.artifact_fingerprints
            )
            checks.extend(checked.checks)
            verify_leases(leases)
            return PluginTransferPreflight(draft, checked, tuple(checks), report, leases)
        except (OSError, TypeError, ValueError) as exc:
            checks.append(WritePreflightCheck("TRANSFER_PREFLIGHT_FAILED", False, f"套用预检失败：{exc}"))
            return PluginTransferPreflight(draft, None, tuple(checks), report)


class PluginTransferWorkload:
    supports_cancel = True

    def __init__(self, checked: PluginTransferPreflight) -> None:
        if not checked.ready:
            raise ValueError("cannot execute a blocked plugin transfer")
        self._checked = checked

    def __call__(self, run_context):
        token = run_context.cancellation
        if token is not None and token.is_cancelled:
            return OperationResult.cancelled(run_id=run_context.ref.run_id)
        try:
            verify_leases(self._checked.leases)
        except (OSError, ValueError) as exc:
            return _failed(str(exc), run_context.ref.run_id)
        guard = TransferCommitGuard(run_context.publish_commit_guard(), self._checked.leases)
        result = HydratedWriteWorkload(self._checked.write_preflight)(_TransferRunContext(run_context, guard))
        if guard.conflict:
            return _failed(guard.conflict, run_context.ref.run_id)
        if result.outcome not in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
            return result
        value = dict(result.value or {})
        value["transfer_report"] = self._checked.report.to_dict()
        if result.outcome is OperationOutcome.COMPLETED:
            diagnostics = result.diagnostics
            if self._checked.report.issues:
                diagnostics += (
                    Diagnostic("TRANSFER_PRESERVED", self._checked.report.summary, severity=DiagnosticSeverity.WARNING),
                )
            return replace(
                result,
                value=value,
                counts=OperationCounts(
                    succeeded=self._checked.report.writable_count, skipped=self._checked.report.preserved_count
                ),
                diagnostics=diagnostics,
            )
        return replace(result, value=value)


class _TransferRunContext:
    def __init__(self, delegate, guard) -> None:
        self._delegate = delegate
        self._guard = guard

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def publish_commit_guard(self):
        return self._guard


def _failed(message, run_id):
    return OperationResult.failed(DomainError(ErrorCategory.CONFLICT, "TRANSFER_FILES_CHANGED", message), run_id=run_id)
