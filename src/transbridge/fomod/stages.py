"""Adapters from existing FOMOD/fileops assets to the typed stage contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
import json
from pathlib import Path
from typing import Protocol

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, OperationOutcome
from transbridge.application.fomod import (
    ArtifactRef,
    FomodStageId,
    StageContext,
    StageExecutionError,
    StageResult,
    StagingPackPublisher,
)
from transbridge.application.tasks import TaskCancelled
from transbridge.fileops import (
    RESOURCE_FILTER_POLICY_VERSION,
    FilterAction,
    FilterDecision,
    FilterRules,
    ResourceRole,
    classify_files,
    diff_directories,
    inspect_archive,
)
from transbridge.fomod.builder import assemble_output
from transbridge.fomod.discovery import FomodExtractionResult, extract_fomod_archive
from transbridge.fomod.xml_fidelity import (
    XmlFidelityReport,
    find_fomod_xml_files,
    parse_fomod_xml,
    patch_and_validate,
)


class PluginTranslationPort(Protocol):
    def translate_plugins(
        self,
        new_root: Path,
        old_root: Path | None,
        *,
        run_id: str,
        target_locale: str,
        ai_enabled: bool,
        cancellation: object | None,
    ) -> PluginTranslationSummary: ...


class XmlTranslationPort(Protocol):
    def translate_xml(
        self,
        new_root: Path,
        old_root: Path | None,
        *,
        target_locale: str,
        ai_enabled: bool,
        cancellation: object | None,
    ) -> XmlTranslationSummary: ...


@dataclass(frozen=True, slots=True)
class PluginTranslationSummary:
    inherited: int = 0
    needs_review: tuple[str, ...] = ()
    dictionary_applied: int = 0
    ai_translated: int = 0
    plugins_processed: int = 0
    unresolved: int = 0
    publish_blockers: tuple[Diagnostic, ...] = ()
    provenance_sources: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        if (
            min(
                self.inherited,
                self.dictionary_applied,
                self.ai_translated,
                self.plugins_processed,
                self.unresolved,
            )
            < 0
        ):
            raise ValueError("plugin translation metrics must not be negative")
        if any(value < 0 for _, value in self.provenance_sources):
            raise ValueError("plugin provenance counts must not be negative")


@dataclass(frozen=True, slots=True)
class XmlTranslationSummary:
    reports: tuple[XmlFidelityReport, ...] = ()

    @property
    def translated(self) -> bool:
        return any(report.changed_nodes for report in self.reports)


class DiscoverStage:
    stage_id = FomodStageId.DISCOVER
    required_artifacts: tuple[str, ...] = ()

    def execute(self, context: StageContext) -> StageResult:
        spec = context.spec
        artifacts = [self._inspect("new_archive_manifest", spec.new_archive, spec.new_archive_hash)]
        if spec.old_archive is not None and spec.old_archive_hash is not None:
            artifacts.append(self._inspect("old_archive_manifest", spec.old_archive, spec.old_archive_hash))
        return StageResult.completed(
            self.stage_id,
            artifacts=tuple(artifacts),
            metrics=(("archive_count", len(artifacts)),),
        )

    @staticmethod
    def _inspect(artifact_id: str, path: str, expected_hash: str) -> ArtifactRef:
        actual_hash = _hash_file(Path(path))
        if actual_hash != expected_hash:
            raise StageExecutionError(
                "FOMOD_INPUT_CHANGED",
                f"input fingerprint changed for {artifact_id}",
            )
        manifest = inspect_archive(path)
        return ArtifactRef(
            artifact_id,
            "archive-manifest",
            path,
            expected_hash,
            attributes=(
                ("format", manifest.archive_format),
                ("members", str(len(manifest.members))),
                ("uncompressed_bytes", str(manifest.total_uncompressed)),
            ),
        )


class ExtractStage:
    stage_id = FomodStageId.EXTRACT
    required_artifacts = ("new_archive_manifest",)

    def execute(self, context: StageContext) -> StageResult:
        new_result = extract_fomod_archive(
            context.spec.new_archive,
            context.workspace / "new",
            cancellation=context.cancellation,
        )
        new_root = _select_root(new_result, "new")
        artifacts = [
            ArtifactRef("new_extracted", "staging-directory", new_result.extraction.dest_dir),
            ArtifactRef("new_root", "mod-root", str(new_root)),
        ]
        extracted_count = new_result.extraction.extracted_count

        if context.spec.old_archive is not None:
            old_result = extract_fomod_archive(
                context.spec.old_archive,
                context.workspace / "old",
                cancellation=context.cancellation,
            )
            old_root = _select_root(old_result, "old")
            artifacts.extend((
                ArtifactRef("old_extracted", "staging-directory", old_result.extraction.dest_dir),
                ArtifactRef("old_root", "mod-root", str(old_root)),
            ))
            extracted_count += old_result.extraction.extracted_count

        return StageResult.completed(
            self.stage_id,
            artifacts=tuple(artifacts),
            metrics=(("extracted_count", extracted_count),),
        )


class DiffStage:
    stage_id = FomodStageId.DIFF
    required_artifacts = ("new_root",)

    def __init__(self, rules: FilterRules | None = None) -> None:
        self._rules = rules or FilterRules()

    def execute(self, context: StageContext) -> StageResult:
        report_path = context.workspace / "diff.json"
        if context.spec.old_archive is None:
            payload = {"added": [], "removed": [], "changed": [], "unchanged": []}
            metrics = (("skipped", True),)
        else:
            result = diff_directories(
                context.require("old_root").location,
                context.require("new_root").location,
                skip_hash_exts=self._rules.strip_exts,
            )
            payload = result.to_dict()
            summary = payload["summary"]
            metrics = tuple((key, int(value)) for key, value in summary.items())
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return StageResult.completed(
            self.stage_id,
            artifacts=(ArtifactRef("diff_report", "diff-report", str(report_path)),),
            metrics=metrics,
        )


class MigrationPlanStage:
    stage_id = FomodStageId.MIGRATE
    required_artifacts = ("new_root", "diff_report")

    def execute(self, context: StageContext) -> StageResult:
        new_root = Path(context.require("new_root").location)
        old_root = Path(context.require("old_root").location) if context.spec.old_archive is not None else None
        items = []
        matched = 0
        for plugin in sorted(
            (path for path in new_root.rglob("*") if path.suffix.casefold() in {".esp", ".esm", ".esl"}),
            key=lambda path: path.as_posix().casefold(),
        ):
            relative = plugin.relative_to(new_root).as_posix()
            old_plugin = old_root / Path(relative) if old_root is not None else None
            old_location = str(old_plugin) if old_plugin is not None and old_plugin.is_file() else None
            matched += int(old_location is not None)
            items.append({"relative_path": relative, "new": str(plugin), "old": old_location})
        plan_path = context.workspace / "migration-plan.json"
        plan_path.write_text(
            json.dumps(items, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return StageResult.completed(
            self.stage_id,
            artifacts=(ArtifactRef("migration_plan", "migration-plan", str(plan_path)),),
            metrics=(("plugins", len(items)), ("old_matches", matched)),
        )


class TranslationStage:
    stage_id = FomodStageId.TRANSLATE
    required_artifacts = ("new_root", "migration_plan")

    def __init__(self, port: PluginTranslationPort | None = None) -> None:
        self._port = port

    def execute(self, context: StageContext) -> StageResult:
        _raise_if_cancelled(context.cancellation)
        new_root = Path(context.require("new_root").location)
        old_root = Path(context.require("old_root").location) if context.spec.old_archive is not None else None
        summary = (
            self._port.translate_plugins(
                new_root,
                old_root,
                run_id=context.spec.run_id,
                target_locale=context.spec.target_locale,
                ai_enabled=context.spec.ai_enabled,
                cancellation=context.cancellation,
            )
            if self._port is not None
            else PluginTranslationSummary()
        )
        _raise_if_cancelled(context.cancellation)
        if summary.publish_blockers:
            return StageResult.failed(
                self.stage_id,
                summary.publish_blockers[0].code,
                summary.publish_blockers[0].message,
            )
        attributes = (
            ("target_locale", context.spec.target_locale),
            ("needs_review", json.dumps(summary.needs_review, ensure_ascii=False)),
            ("provenance", json.dumps(dict(summary.provenance_sources), ensure_ascii=False)),
        )
        return StageResult.completed(
            self.stage_id,
            artifacts=(
                ArtifactRef(
                    "translated_root",
                    "translated-staging-directory",
                    str(new_root),
                    attributes=attributes,
                ),
            ),
            metrics=(
                ("inherited", summary.inherited),
                ("dictionary_applied", summary.dictionary_applied),
                ("ai_translated", summary.ai_translated),
                ("plugins_processed", summary.plugins_processed),
                ("unresolved", summary.unresolved),
            ),
        )


class XmlStage:
    stage_id = FomodStageId.XML
    required_artifacts = ("translated_root",)

    def __init__(self, port: XmlTranslationPort | None = None) -> None:
        self._port = port

    def execute(self, context: StageContext) -> StageResult:
        _raise_if_cancelled(context.cancellation)
        new_root = Path(context.require("translated_root").location)
        old_root = Path(context.require("old_root").location) if context.spec.old_archive is not None else None
        if self._port is not None:
            parameters = inspect.signature(self._port.translate_xml).parameters
            options = {
                "target_locale": context.spec.target_locale,
                "cancellation": context.cancellation,
            }
            if "ai_enabled" in parameters:
                options["ai_enabled"] = context.spec.ai_enabled
            result = self._port.translate_xml(new_root, old_root, **options)
            summary = result if isinstance(result, XmlTranslationSummary) else XmlTranslationSummary()
            translated = summary.translated or result is True
        else:
            reports = []
            for path in find_fomod_xml_files(new_root):
                snapshot = parse_fomod_xml(path.read_bytes())
                _, report = patch_and_validate(snapshot, ())
                reports.append(report)
            summary = XmlTranslationSummary(tuple(reports))
            translated = summary.translated
        diagnostics = tuple(diagnostic for report in summary.reports for diagnostic in report.diagnostics)
        if not summary.reports:
            diagnostics = (
                *diagnostics,
                Diagnostic(
                    "FOMOD_XML_NOT_PRESENT",
                    "No supported FOMOD XML metadata file was present",
                    severity=DiagnosticSeverity.INFO,
                ),
            )
        _raise_if_cancelled(context.cancellation)
        report_path = context.workspace / "xml-fidelity-report.json"
        report_path.write_text(
            json.dumps(
                {
                    "policy_version": "fomod-xml-semantic-v2",
                    "files": [report.to_dict() for report in summary.reports],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return StageResult.completed(
            self.stage_id,
            artifacts=(
                ArtifactRef("xml_root", "xml-updated-staging-directory", str(new_root)),
                ArtifactRef("xml_fidelity_report", "xml-fidelity-report", str(report_path)),
            ),
            diagnostics=diagnostics,
            metrics=(
                ("translated", translated),
                ("xml_files", len(summary.reports)),
                ("changed_nodes", sum(len(item.changed_nodes) for item in summary.reports)),
                ("target_locale", context.spec.target_locale),
            ),
        )


class FilterStage:
    stage_id = FomodStageId.FILTER
    required_artifacts = ("xml_root", "xml_fidelity_report")

    def __init__(self, rules: FilterRules | None = None) -> None:
        self._rules = rules or FilterRules()

    def execute(self, context: StageContext) -> StageResult:
        root = Path(context.require("xml_root").location)
        relative_files = [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()]
        xml_report = json.loads(Path(context.require("xml_fidelity_report").location).read_text(encoding="utf-8"))
        references = tuple(
            reference for report in xml_report.get("files", ()) for reference in report.get("preserved_resources", ())
        )
        decisions = classify_files(relative_files, self._rules, references=references)
        source_fingerprint = _directory_manifest_fingerprint(root)
        manifest_path = context.workspace / "filter-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "policy_version": RESOURCE_FILTER_POLICY_VERSION,
                    "source_fingerprint": source_fingerprint,
                    "references": list(references),
                    "decisions": [item.to_dict() for item in decisions],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        kept = sum(item.action is FilterAction.KEEP for item in decisions)
        stripped = len(decisions) - kept
        return StageResult.completed(
            self.stage_id,
            artifacts=(ArtifactRef("filter_manifest", "filter-manifest", str(manifest_path)),),
            metrics=(("kept_count", kept), ("stripped_count", stripped)),
        )


class BuildStage:
    stage_id = FomodStageId.BUILD
    required_artifacts = ("xml_root", "filter_manifest")

    def __init__(self, rules: FilterRules | None = None) -> None:
        self._rules = rules or FilterRules()

    def execute(self, context: StageContext) -> StageResult:
        _raise_if_cancelled(context.cancellation)
        output = context.workspace / "build"
        source = Path(context.require("xml_root").location)
        manifest = json.loads(Path(context.require("filter_manifest").location).read_text(encoding="utf-8"))
        if manifest.get("policy_version") != RESOURCE_FILTER_POLICY_VERSION:
            raise StageExecutionError("FOMOD_FILTER_POLICY_STALE", "filter policy version changed")
        if manifest.get("source_fingerprint") != _directory_manifest_fingerprint(source):
            raise StageExecutionError("FOMOD_FILTER_SOURCE_CHANGED", "source changed after filtering")
        decision_payload = manifest.get("decisions", ())
        if any(item.get("policy_version") != RESOURCE_FILTER_POLICY_VERSION for item in decision_payload):
            raise StageExecutionError("FOMOD_FILTER_POLICY_STALE", "filter decision policy version changed")
        decisions = tuple(
            FilterDecision(
                str(item["path"]),
                ResourceRole(item["role"]),
                FilterAction(item["action"]),
                str(item["reason"]),
                str(item["policy_version"]),
            )
            for item in decision_payload
        )
        result = assemble_output(str(source), str(output), self._rules, decisions=decisions)
        _raise_if_cancelled(context.cancellation)
        return StageResult.completed(
            self.stage_id,
            artifacts=(ArtifactRef("build_directory", "build-directory", str(output)),),
            metrics=(
                ("kept_count", int(result["kept_count"])),
                ("stripped_count", int(result["stripped_count"])),
            ),
        )


class PublishStage:
    stage_id = FomodStageId.PUBLISH
    required_artifacts = ("build_directory",)

    def __init__(self, publisher: StagingPackPublisher | None = None) -> None:
        self._publisher = publisher or StagingPackPublisher()

    def execute(self, context: StageContext) -> StageResult:
        _raise_if_cancelled(context.cancellation)
        build = context.require("build_directory")
        result = self._publisher.publish(
            context.spec,
            build.location,
            cancellation=context.cancellation,
            commit_guard=lambda run_id, mutation: context.commit_guard.commit(run_id, mutation),
        )
        if result.outcome in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
            artifacts = [
                ArtifactRef(
                    "published_archive",
                    "published-archive",
                    result.target,
                    result.artifact_sha256,
                    attributes=(
                        ("run_id", context.spec.run_id),
                        ("target_locale", context.spec.target_locale),
                    ),
                )
            ]
            if result.manifest_path is not None:
                artifacts.append(
                    ArtifactRef(
                        "publish_manifest",
                        "publish-manifest",
                        result.manifest_path,
                    )
                )
            diagnostics = tuple(Diagnostic(d.code, d.message, severity=d.severity) for d in result.diagnostics)
            if result.outcome is OperationOutcome.PARTIAL:
                return StageResult(
                    self.stage_id,
                    OperationOutcome.PARTIAL,
                    artifacts=tuple(artifacts),
                    diagnostics=diagnostics,
                    metrics=(("bytes", int(result.artifact_size or 0)),),
                )
            return StageResult.completed(
                self.stage_id,
                artifacts=tuple(artifacts),
                metrics=(("bytes", int(result.artifact_size or 0)),),
            )
        if result.outcome is OperationOutcome.CANCELLED:
            return StageResult.cancelled(self.stage_id, result.message)
        diagnostics = tuple(Diagnostic(d.code, d.message, severity=d.severity) for d in result.diagnostics) or (
            Diagnostic(result.code, result.message),
        )
        return StageResult(self.stage_id, OperationOutcome.FAILED, diagnostics=diagnostics)


def default_stages(
    *,
    rules: FilterRules | None = None,
    plugin_port: PluginTranslationPort | None = None,
    xml_port: XmlTranslationPort | None = None,
) -> tuple:
    active_rules = rules or FilterRules()
    return (
        DiscoverStage(),
        ExtractStage(),
        DiffStage(active_rules),
        MigrationPlanStage(),
        TranslationStage(plugin_port),
        XmlStage(xml_port),
        FilterStage(active_rules),
        BuildStage(active_rules),
        PublishStage(),
    )


def _select_root(result: FomodExtractionResult, label: str) -> Path:
    roots = result.roots
    if roots.selected is None:
        if roots.confirmation_required:
            candidates = ", ".join(candidate.relative_path for candidate in roots.candidates)
            raise StageExecutionError(
                "FOMOD_ROOT_CONFIRMATION_REQUIRED",
                f"{label} archive has multiple root candidates: {candidates}",
            )
        raise StageExecutionError(
            "FOMOD_ROOT_NOT_FOUND",
            f"{label} archive has no recognizable MOD root",
        )
    base = Path(result.extraction.dest_dir)
    return base if roots.selected.relative_path == "." else base / roots.selected.relative_path


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_manifest_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_hash_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _raise_if_cancelled(signal: object | None) -> None:
    if signal is None:
        return
    state = getattr(signal, "is_cancelled", None)
    if state is None:
        is_set = getattr(signal, "is_set", None)
        cancelled = bool(is_set()) if callable(is_set) else False
    else:
        cancelled = bool(state() if callable(state) else state)
    if cancelled:
        raise TaskCancelled("FOMOD stage cancelled")
