"""Explicit JSON exports from immutable sources, separate from native writeback."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)

from .contracts import FormatId, SourceDescriptor, WriteRequest
from .dsd_projection import restore_extracted_dsd_locator, restore_plugin_dsd_locators
from .operation_write import HydratedWriteDraft, WritePreflightCheck, _operation_result, _translation_entries
from .paratranz import ParatranzJsonAdapter
from .publish import ConflictPolicy, FileFingerprint, OsPublishFilesystem, PublishCoordinator, PublishTarget
from .publish.models import ValidationReport
from .publish.target_directory import output_parent_available
from .publish.validators import FormatAdapterRenderer, FormatRoundTripValidator

JSON_EXPORT_FORMATS = frozenset({FormatId.JSON_DSD, FormatId.JSON_PARATRANZ})
JSON_EXPORT_LABELS = (("json.paratranz", "ParaTranz JSON (.json)"), ("json.dsd", "DSD JSON (.json)"))


@dataclass(frozen=True, slots=True)
class JsonExportDraft:
    sources: tuple[HydratedWriteDraft, ...]
    format_id: FormatId
    output_directory: str = ""
    is_batch: bool = False

    def __post_init__(self):
        if self.format_id not in JSON_EXPORT_FORMATS or not self.sources:
            raise ValueError("JSON export needs sources and an explicit JSON format")


@dataclass(frozen=True, slots=True)
class JsonExportPreflight:
    draft: JsonExportDraft
    checks: tuple[WritePreflightCheck, ...]
    requests: tuple[WriteRequest, ...]
    fingerprints: tuple[FileFingerprint, ...]
    dsd_payloads: tuple[bytes, ...]

    @property
    def ready(self):
        return all(check.passed or check.warning for check in self.checks)


def preflight_json_export(draft: JsonExportDraft, projection_service) -> JsonExportPreflight:
    """Capture projections and destinations once; publish guards recheck at commit."""
    fs = OsPublishFilesystem()
    checks, requests, fingerprints, payloads = [], [], [], []
    targets = set()
    frozen = projection_service.freeze_projection(draft.sources[0])
    for index, source in enumerate(draft.sources):
        target = source.target_path
        valid_target = bool(target.strip()) and Path(target).suffix.lower() == ".json"
        canonical = os.path.normcase(fs.canonicalize(target)) if target.strip() else ""
        checks.append(WritePreflightCheck(f"JSON_PATH_{index}", valid_target, "请选择 .json 输出文件。"))
        checks.append(
            WritePreflightCheck(f"JSON_COLLISION_{index}", canonical not in targets, "多个来源的 JSON 输出重名。")
        )
        targets.add(canonical)
        fingerprint = fs.fingerprint(target) if valid_target else FileFingerprint.missing()
        fingerprints.append(fingerprint)
        checks.append(
            WritePreflightCheck(
                f"OVERWRITE_CONFIRMED_{index}",
                not fingerprint.exists or source.conflict_policy is ConflictPolicy.EXPLICIT_OVERWRITE,
                f"目标已存在：{target}",
            )
        )
        checks.append(
            WritePreflightCheck(
                f"JSON_DIRECTORY_{index}",
                valid_target and output_parent_available(canonical) and fs.atomic_replace_supported(target),
                "输出目录不可写或不支持原子替换。",
            )
        )
        entries = _translation_entries(source.entries)
        if frozen is not None:
            entries, diagnostics = frozen.project_entries(entries)
            checks.extend(WritePreflightCheck(item.code, False, item.message, warning=True) for item in diagnostics)
        request = WriteRequest(
            SourceDescriptor(target or "unselected.json"),
            draft.format_id,
            entries,
            source.variant_revision,
            source.context,
            source_snapshot=source.source_snapshot,
        )
        requests.append(request)
        payload = b""
        if draft.format_id is FormatId.JSON_PARATRANZ:
            validation = ParatranzJsonAdapter().validate_write(request)
            checks.extend(WritePreflightCheck(item.code, False, item.message) for item in validation.diagnostics)
        else:
            try:
                payload = _dsd_payload(source, entries)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                checks.append(WritePreflightCheck("DSD_LOCATOR_REQUIRED", False, str(exc)))
        payloads.append(payload)
    return JsonExportPreflight(draft, tuple(checks), tuple(requests), tuple(fingerprints), tuple(payloads))


def _dsd_payload(source, entries):
    """DSD needs plugin-qualified locators; never invent them from an EET filename."""
    known = {}
    if source.source_snapshot.format_id not in {FormatId.PLUGIN_SSE, FormatId.JSON_DSD}:
        raise ValueError("该来源没有 DSD 所需的插件归属信息；请从原插件导出 DSD JSON。")
    if source.source_snapshot.format_id is FormatId.PLUGIN_SSE:
        entries = restore_plugin_dsd_locators(entries, source.source_snapshot)
    elif source.source_snapshot.format_id is FormatId.JSON_DSD:
        from .json_source import parse_json_source

        content = source.source_snapshot.content
        if content is None:
            entries = tuple(restore_extracted_dsd_locator(entry) for entry in entries)
        else:
            parsed = parse_json_source(source.source_snapshot.source.uri, {"format": "dsd"}, content=content)
            known = {(entry.key, entry.original): entry for entry in parsed.collection}
    result = []
    for entry in entries:
        if not entry.translation or entry.stage == -1:
            continue
        original = known.get((entry.key, entry.original)) if known else entry
        if original is None:
            raise ValueError("当前词条与 DSD 来源快照不一致，请重新打开工程。")
        if not original.form_id_with_plugin or not original.dsd_type:
            raise ValueError("该来源缺少 DSD 所需的插件名、FormID 或字段信息；请从原插件导出 DSD JSON。")
        if original.dsd_type == "GMST DATA" and not original.editor_id:
            raise ValueError("GMST DATA 缺少 EditorID，无法导出 DSD JSON。")
        if entry.stage not in {-1, 0, 1, 2, 3, 5, 9}:
            raise ValueError("条目翻译阶段无效，无法导出 DSD JSON。")
        result.append(replace(original, translation=entry.translation).to_dsd_dict())
    return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


class _DsdArtifact:
    def __init__(self, payload: bytes):
        self.payload = payload

    def render(self, request, staging_path):
        Path(staging_path).write_bytes(self.payload)
        return OperationResult.completed((staging_path,), run_id=request.context.run_id)

    def validate(self, request, staging_path):
        data = Path(staging_path).read_bytes()
        records = json.loads(data)
        valid = data == self.payload and isinstance(records, list)
        return ValidationReport(
            valid,
            FormatId.JSON_DSD,
            True,
            True,
            valid,
            len(records),
            hashlib.sha256(data).hexdigest(),
            "VALID" if valid else "DSD_FIDELITY_MISMATCH",
            "DSD JSON 完整内容回读验证",
        )


class JsonExportWorkload:
    supports_cancel = True

    def __init__(self, checked: JsonExportPreflight):
        if not checked.ready:
            raise ValueError("JSON export is blocked")
        self.checked = checked

    def __call__(self, run_context):
        fs = OsPublishFilesystem()
        outcomes, artifacts, diagnostics = [], [], []
        succeeded = failed = cancelled = 0
        for index, request in enumerate(self.checked.requests):
            source = self.checked.draft.sources[index]
            current = replace(
                request,
                context=replace(request.context, run_id=run_context.ref.run_id),
                cancellation=run_context.cancellation,
            )
            adapter = ParatranzJsonAdapter()
            dsd = _DsdArtifact(self.checked.dsd_payloads[index])
            result = PublishCoordinator(fs).publish(
                current,
                PublishTarget(
                    source.target_path, source.conflict_policy, source.backup_policy, self.checked.fingerprints[index]
                ),
                renderer=dsd if request.format_id is FormatId.JSON_DSD else FormatAdapterRenderer(adapter),
                validator=dsd if request.format_id is FormatId.JSON_DSD else FormatRoundTripValidator(adapter, fs),
                commit_guard=run_context.publish_commit_guard(),
            )
            converted = _operation_result(result, run_context.ref.run_id)
            if not self.checked.draft.is_batch:
                return converted
            artifacts.extend(converted.artifact_refs)
            diagnostics.extend(converted.diagnostics)
            succeeded += int(result.published)
            failed += int(result.outcome in {OperationOutcome.FAILED, OperationOutcome.PARTIAL})
            cancelled += int(result.outcome is OperationOutcome.CANCELLED)
            outcomes.append({
                "object_ref": source.target_path,
                "label": Path(source.target_path).name,
                "status": "succeeded"
                if result.outcome is OperationOutcome.COMPLETED
                else "cancelled"
                if result.outcome is OperationOutcome.CANCELLED
                else "failed",
                "code": result.code,
                "retryable": False,
            })
        counts = OperationCounts(succeeded=succeeded, failed=failed, cancelled=cancelled)
        outcome = (
            OperationOutcome.COMPLETED
            if not failed and not cancelled
            else (
                OperationOutcome.PARTIAL
                if succeeded
                else OperationOutcome.FAILED
                if failed
                else OperationOutcome.CANCELLED
            )
        )
        if outcome in {OperationOutcome.FAILED, OperationOutcome.PARTIAL} and not any(
            item.severity is DiagnosticSeverity.ERROR for item in diagnostics
        ):
            diagnostics.append(Diagnostic("JSON_EXPORT_FAILED", "部分 JSON 文件未导出。"))
        return OperationResult(
            outcome,
            {"outcomes": tuple(outcomes)} if succeeded else None,
            tuple(diagnostics),
            counts,
            artifact_refs=tuple(artifacts),
            run_id=run_context.ref.run_id,
        )
