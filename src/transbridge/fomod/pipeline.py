"""FOMOD 流水线编排：解包→diff→逐插件[键对齐→词典兜底→AI翻译→写回]→界面文本翻译→组装→打包。

纯 Python，无 PyQt 依赖（ADR-008）。翻译来源按 ADR-014 优先级：
① 旧归档（键对齐）→ ② 词典（文本/键兜底）→ ③ AI 翻译（AutoTranslator）。
运行时上下文（llm_config / tm_manager）由调用方（GUI）注入。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from uuid import uuid4

from transbridge.application.contracts import OperationOutcome
from transbridge.application.fomod import (
    FomodPolicies,
    FomodRunSpec,
    FomodStageId,
    PipelineEngine,
    PipelineResult as TypedPipelineResult,
    StageEventSink,
)
from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY, Stage
from transbridge.application.tasks import TaskCancelled
from transbridge.fomod.stages import (
    PluginTranslationSummary,
    XmlTranslationSummary,
    default_stages,
)
from transbridge.fomod.xml_fidelity import find_fomod_xml_files, process_fomod_xml_file


@dataclass
class PipelineResult:
    extracted_count: int = 0
    diff: dict = field(default_factory=dict)
    inherited: int = 0  # 键对齐继承
    needs_review: list = field(default_factory=list)
    dict_applied: int = 0  # 词典套用命中
    ai_translated: int = 0  # AI 翻译条数
    plugins_processed: int = 0  # 处理的插件数
    unresolved: int = 0
    publish_blockers: list = field(default_factory=list)
    provenance_sources: dict[str, int] = field(default_factory=dict)
    kept_count: int = 0
    stripped_count: int = 0
    archive_path: str = ""

    def to_dict(self) -> dict:
        return {
            "extracted_count": self.extracted_count,
            "diff": self.diff,
            "inherited": self.inherited,
            "needs_review": self.needs_review,
            "dict_applied": self.dict_applied,
            "ai_translated": self.ai_translated,
            "plugins_processed": self.plugins_processed,
            "unresolved": self.unresolved,
            "publish_blockers": [item.to_dict() for item in self.publish_blockers],
            "provenance_sources": dict(self.provenance_sources),
            "kept_count": self.kept_count,
            "stripped_count": self.stripped_count,
            "archive_path": self.archive_path,
        }


_PLUGIN_EXTS = {".esp", ".esm", ".esl"}


def _select_fomod_ai_entry_ids(collection) -> list[str]:
    """Return only FOMOD's untranslated targets allowed by StagePolicy."""
    return [
        entry.key
        for entry in collection
        if entry.stage == Stage.UNTRANSLATED.value
        and not entry.translation
        and DEFAULT_STAGE_POLICY.allows_ai(entry.stage, entry.translation, original=entry.original)
    ]


class FomodPipeline:
    """FOMOD 翻译流水线编排器。"""

    def __init__(
        self,
        rules=None,
        llm_config=None,
        tm_manager=None,
        *,
        stage_event_sink: StageEventSink | None = None,
    ):
        self._rules = rules
        self._llm_config = llm_config
        self._tm_manager = tm_manager
        self._stage_event_sink = stage_event_sink
        self._last_report: TypedPipelineResult | None = None

    @property
    def last_report(self) -> TypedPipelineResult | None:
        return self._last_report

    def run(
        self,
        new_archive: str,
        output_archive: str,
        *,
        old_archive: str | None = None,
        work_dir: str | None = None,
        fmt: str = "zip",
        target_lang: str | None = None,
        ai_enabled: bool = True,
        progress_callback=None,
        stop_event: threading.Event | None = None,
    ) -> PipelineResult:
        """Compatibility facade delegating to the typed application workload."""
        if target_lang is None or not target_lang.strip():
            raise ValueError("target_lang is required; FOMOD does not apply a locale fallback")
        new_path = Path(new_archive)
        old_path = Path(old_archive) if old_archive is not None else None
        workspace_root = work_dir or tempfile.mkdtemp(prefix="tb_fomod_")
        run_id = uuid4().hex
        spec = FomodRunSpec(
            run_id=run_id,
            new_archive=str(new_path),
            new_archive_hash=_hash_file(new_path),
            old_archive=None if old_path is None else str(old_path),
            old_archive_hash=None if old_path is None else _hash_file(old_path),
            output_archive=output_archive,
            target_locale=target_lang,
            config_hash=_config_hash(self._llm_config, target_lang),
            policies=FomodPolicies(),
            output_format=fmt,
            workspace_root=workspace_root,
            ai_enabled=ai_enabled,
        )
        stop = stop_event or threading.Event()
        plugin_port = _LegacyPluginPort(self, progress_callback)
        xml_port = _LegacyXmlPort(self)
        engine = PipelineEngine(
            default_stages(
                rules=self._rules,
                plugin_port=plugin_port,
                xml_port=xml_port,
            ),
            event_sink=self._stage_event_sink,
        )
        report = engine.run(spec, stop)
        self._last_report = report
        if report.outcome is OperationOutcome.CANCELLED:
            raise TaskCancelled("FOMOD pipeline cancelled")
        if report.outcome is OperationOutcome.FAILED:
            codes = ",".join(diagnostic.code for diagnostic in report.diagnostics)
            raise RuntimeError(f"FOMOD_PIPELINE_FAILED:{codes}")
        return _legacy_result(report)

    # ── 逐插件翻译 ───────────────────────────────────────────────

    def _translate_plugins(
        self,
        new_dir: Path,
        old_dir,
        result,
        progress_callback,
        stop_event,
        ai_enabled=True,
        target_locale: str | None = None,
        run_id: str | None = None,
    ):
        from transbridge.application.contracts import Diagnostic, RequestContext
        from transbridge.application.fomod import (
            CandidateOrigin,
            CommitFomodCandidates,
            FomodCandidatePlanner,
            FomodCandidateSet,
            FomodTranslationCandidate,
        )
        from transbridge.application.io import EntryKey, Provenance
        from transbridge.converter.translation_entry_collection import TranslationEntryCollection
        from transbridge.migrator import KeyMigrationPlan, plan_migration
        from transbridge.parser.plugin_parser import PluginParser
        from transbridge.translation_memory import (
            TranslationMemoryManager,
            TranslationMemoryQueryService,
        )

        parser = PluginParser()
        if not target_locale or not target_locale.strip():
            raise ValueError("target_locale is required for FOMOD translation planning")
        if not run_id or not run_id.strip():
            raise ValueError("run_id is required for FOMOD translation provenance")
        tm = self._tm_manager or TranslationMemoryManager()
        if self._tm_manager is None:
            tm.load()

        plugins = [p for p in new_dir.rglob("*") if p.suffix.lower() in _PLUGIN_EXTS]
        for i, esp_path in enumerate(plugins):
            if _cancelled(stop_event):
                raise TaskCancelled("FOMOD plugin translation cancelled")
            rel = esp_path.relative_to(new_dir)
            # 解析新版
            namespace = _plugin_namespace(rel)
            new_entries = tuple(
                replace(entry, entry_key=EntryKey(namespace, entry.key)) for entry in parser.parse_plugin(esp_path)
            )
            new_collection = TranslationEntryCollection(new_entries)
            new_fingerprint = _hash_file(esp_path)

            migration = KeyMigrationPlan((), tuple(entry.identity for entry in new_entries), ())
            if old_dir is not None:
                old_esp = old_dir / rel
                if old_esp.exists():
                    old_entries = tuple(
                        replace(entry, entry_key=EntryKey(namespace, entry.key))
                        for entry in parser.parse_plugin(old_esp)
                    )
                    migration = plan_migration(
                        old_entries,
                        new_entries,
                        old_fingerprint=_hash_file(old_esp),
                        new_fingerprint=new_fingerprint,
                        cancellation=stop_event,
                    )

            planned = FomodCandidatePlanner(TranslationMemoryQueryService(tm)).plan(
                run_id=run_id,
                entries=tuple(entry.snapshot() for entry in new_entries),
                migration=migration,
                source_locale="en_US",
                target_locale=target_locale or "",
                source_fingerprint=new_fingerprint,
                cancellation=stop_event,
            )
            if planned.cancelled:
                raise TaskCancelled("FOMOD plugin candidate planning cancelled")
            result.publish_blockers.extend(planned.blockers)
            if planned.blockers:
                return
            result.needs_review.extend(conflict.entry_key.serialize() for conflict in planned.conflicts)
            if planned.conflicts:
                result.publish_blockers.append(
                    Diagnostic(
                        "FOMOD_TRANSLATION_CONFIRMATION_REQUIRED",
                        "Translation candidates conflict or are stale and require explicit arbitration.",
                        details=(("count", len(planned.conflicts)),),
                    )
                )
                return

            selected_by_key = {item.entry_key: item for item in planned.selected}
            working_entries = tuple(
                replace(
                    entry,
                    translation=selected_by_key[entry.identity].translation,
                    stage=selected_by_key[entry.identity].resulting_stage,
                )
                if entry.identity in selected_by_key
                else replace(entry)
                for entry in new_entries
            )
            working_collection = TranslationEntryCollection(working_entries)

            ai_candidates: list[FomodTranslationCandidate] = []
            if ai_enabled and planned.unresolved:
                self._ai_translate(
                    working_collection,
                    esp_path,
                    stop_event,
                    target_locale=target_locale,
                    entry_keys=frozenset(planned.unresolved),
                )
                for key in planned.unresolved:
                    translated = working_collection.get(key)
                    original = new_collection.get(key)
                    if translated is None or original is None or not translated.translation:
                        continue
                    ai_candidates.append(
                        FomodTranslationCandidate(
                            key,
                            original.revision,
                            translated.translation,
                            translated.stage,
                            CandidateOrigin.AI,
                            (Provenance(planned.run_id, "fomod-ai", "ai-fallback"),),
                            ("migration_and_tm_unresolved",),
                        )
                    )

            resolved_ai = {item.entry_key for item in ai_candidates}
            final_candidates = FomodCandidateSet(
                planned.run_id,
                (*planned.selected, *ai_candidates),
                planned.conflicts,
                tuple(key for key in planned.unresolved if key not in resolved_ai),
                planned.blockers,
                planned.diagnostics,
            )
            commit = CommitFomodCandidates(new_collection).execute(
                final_candidates,
                RequestContext(
                    "fomod-pipeline",
                    run_id=planned.run_id,
                    permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
                ),
                stop_event,
            )
            if commit.outcome is OperationOutcome.CANCELLED:
                raise TaskCancelled("FOMOD plugin candidate commit cancelled")
            if commit.outcome is OperationOutcome.FAILED:
                codes = ",".join(item.code for item in commit.diagnostics)
                raise RuntimeError(f"FOMOD_CANDIDATE_COMMIT_FAILED:{codes}")
            result.inherited += sum(item.origin is CandidateOrigin.KEY_MIGRATION for item in final_candidates.selected)
            result.dict_applied += sum(
                item.origin is CandidateOrigin.TRANSLATION_MEMORY for item in final_candidates.selected
            )
            result.ai_translated += len(ai_candidates)
            result.unresolved += len(final_candidates.unresolved) + len(final_candidates.conflicts)
            for candidate in final_candidates.selected:
                for provenance in candidate.source_chain:
                    result.provenance_sources[provenance.source] = (
                        result.provenance_sources.get(provenance.source, 0) + 1
                    )

            # 写回
            self._write_back(esp_path, new_collection)
            result.plugins_processed += 1

            if progress_callback:
                progress_callback(i + 1, len(plugins), f"已处理 {rel}")

    def _ai_translate(
        self,
        collection,
        esp_path,
        stop_event,
        *,
        target_locale: str | None = None,
        entry_keys: frozenset | None = None,
    ) -> int:
        """用 AutoTranslator 翻译 stage=0 且无译文的条目，返回翻译条数。"""
        if self._llm_config is None:
            return 0
        untranslated = _select_fomod_ai_entry_ids(collection)
        if entry_keys is not None:
            untranslated = [
                key
                for key in untranslated
                if (entry := collection.get(key)) is not None and entry.identity in entry_keys
            ]
        if not untranslated:
            return 0
        from transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig

        llm_config = _llm_config_for_locale(self._llm_config, target_locale)
        cfg = TranslatorConfig(llm_config=llm_config, esp_path=str(esp_path))
        translator = AutoTranslator(cfg)
        tr = translator.translate(
            collection,
            untranslated,
            progress_callback=lambda *a: None,
            stop_event=_CancellationEventView(stop_event),
        )
        if tr.failed_count:
            raise RuntimeError(f"FOMOD_AI_TRANSLATION_PARTIAL:{tr.failed_count}")
        if _cancelled(stop_event):
            raise TaskCancelled("FOMOD AI translation cancelled")
        return tr.success_count

    def _write_back(self, esp_path, collection):
        """通过统一 I/O use case 写回；失败不得伪装为已完成流水线。"""
        from transbridge.application.contracts import OperationOutcome, RequestContext
        from transbridge.application.io import (
            FormatId,
            ParseRequest,
            SourceDescriptor,
            TranslationIoUseCase,
            WriteRequest,
        )

        source = Path(esp_path)
        use_case = TranslationIoUseCase()
        context = RequestContext("fomod-pipeline")
        parsed = use_case.parse(
            ParseRequest(
                SourceDescriptor(str(source), source.name, source.stat().st_size),
                context,
                FormatId.PLUGIN_SSE,
                options=(("language", "english"),),
            )
        )
        if parsed.outcome not in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
            codes = ",".join(item.code for item in parsed.diagnostics)
            raise RuntimeError(f"FOMOD_PLUGIN_PARSE_FAILED:{codes}")

        staged_entries = []
        for parsed_entry in parsed.entries:
            current = collection.get(parsed_entry.key)
            if current is None:
                staged_entries.append(parsed_entry)
                continue
            staged_entries.append(
                replace(
                    parsed_entry,
                    translation=current.translation,
                    stage=current.stage,
                )
            )
        written = use_case.write(
            WriteRequest(
                SourceDescriptor(str(source), source.name),
                FormatId.PLUGIN_SSE,
                tuple(staged_entries),
                collection.collection_revision.value,
                context,
                source_snapshot=parsed.source_snapshot,
                options=(("language", "english"),),
            )
        )
        if written.outcome is not OperationOutcome.COMPLETED:
            codes = ",".join(item.code for item in written.diagnostics)
            raise RuntimeError(f"FOMOD_PLUGIN_WRITE_FAILED:{codes}")

    # ── 界面文本翻译 ─────────────────────────────────────────────

    def _translate_fomod_xml(
        self,
        new_dir: Path,
        old_dir: Path | None,
        target_lang: str,
        *,
        ai_enabled: bool,
        cancellation: object | None,
    ) -> XmlTranslationSummary:
        llm = None
        if ai_enabled and self._llm_config is not None:
            from transbridge.infra.llm_client import create_llm_client

            llm = create_llm_client(_llm_config_for_locale(self._llm_config, target_lang))
        reports = []
        for xml_path in find_fomod_xml_files(new_dir):
            relative = xml_path.relative_to(new_dir)
            old_path = old_dir / relative if old_dir is not None else None
            reports.append(
                process_fomod_xml_file(
                    xml_path,
                    old_path=old_path,
                    llm=llm,
                    target_locale=target_lang,
                    cancellation=cancellation,
                )
            )
        return XmlTranslationSummary(tuple(reports))


class _LegacyPluginPort:
    def __init__(self, pipeline: FomodPipeline, progress_callback) -> None:
        self._pipeline = pipeline
        self._progress_callback = progress_callback

    def translate_plugins(
        self,
        new_root: Path,
        old_root: Path | None,
        *,
        run_id: str,
        target_locale: str,
        ai_enabled: bool,
        cancellation: object | None,
    ) -> PluginTranslationSummary:
        result = PipelineResult()
        self._pipeline._translate_plugins(
            new_root,
            old_root,
            result,
            self._progress_callback,
            cancellation,
            ai_enabled,
            target_locale,
            run_id,
        )
        return PluginTranslationSummary(
            inherited=result.inherited,
            needs_review=tuple(str(item) for item in result.needs_review),
            dictionary_applied=result.dict_applied,
            ai_translated=result.ai_translated,
            plugins_processed=result.plugins_processed,
            unresolved=result.unresolved,
            publish_blockers=tuple(result.publish_blockers),
            provenance_sources=tuple(sorted(result.provenance_sources.items())),
        )


class _LegacyXmlPort:
    def __init__(self, pipeline: FomodPipeline) -> None:
        self._pipeline = pipeline

    def translate_xml(
        self,
        new_root: Path,
        old_root: Path | None,
        *,
        target_locale: str,
        ai_enabled: bool,
        cancellation: object | None,
    ) -> XmlTranslationSummary:
        _raise_if_cancelled(cancellation)
        summary = self._pipeline._translate_fomod_xml(
            new_root,
            old_root,
            target_locale,
            ai_enabled=ai_enabled,
            cancellation=cancellation,
        )
        _raise_if_cancelled(cancellation)
        return summary


class _CancellationEventView:
    """Expose TaskRuntime tokens through the legacy threading.Event surface."""

    def __init__(self, signal: object | None) -> None:
        self._signal = signal

    def is_set(self) -> bool:
        return _cancelled(self._signal)

    def wait(self, timeout: float | None = None) -> bool:
        wait = getattr(self._signal, "wait", None)
        if callable(wait):
            return bool(wait(timeout))
        return self.is_set()


def _legacy_result(report: TypedPipelineResult) -> PipelineResult:
    result = PipelineResult(
        extracted_count=int(report.metric(FomodStageId.EXTRACT, "extracted_count", 0)),
        inherited=int(report.metric(FomodStageId.TRANSLATE, "inherited", 0)),
        dict_applied=int(report.metric(FomodStageId.TRANSLATE, "dictionary_applied", 0)),
        ai_translated=int(report.metric(FomodStageId.TRANSLATE, "ai_translated", 0)),
        plugins_processed=int(report.metric(FomodStageId.TRANSLATE, "plugins_processed", 0)),
        unresolved=int(report.metric(FomodStageId.TRANSLATE, "unresolved", 0)),
        kept_count=int(report.metric(FomodStageId.BUILD, "kept_count", 0)),
        stripped_count=int(report.metric(FomodStageId.BUILD, "stripped_count", 0)),
    )
    by_id = {artifact.artifact_id: artifact for artifact in report.artifacts}
    diff = by_id.get("diff_report")
    if diff is not None and not report.metric(FomodStageId.DIFF, "skipped", False):
        result.diff = json.loads(Path(diff.location).read_text(encoding="utf-8"))
    translated = by_id.get("translated_root")
    if translated is not None:
        result.needs_review = list(json.loads(translated.attribute("needs_review") or "[]"))
        result.provenance_sources = dict(json.loads(translated.attribute("provenance") or "{}"))
    published = by_id.get("published_archive")
    if published is not None:
        result.archive_path = published.location
    return result


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _plugin_namespace(relative_path: Path):
    """Stable non-path namespace shared by old/new versions of one plugin slot."""
    from transbridge.application.io import SourceNamespace

    stable_id = hashlib.sha256(relative_path.as_posix().casefold().encode()).hexdigest()[:32]
    return SourceNamespace(f"source:plugin:{stable_id}")


def _config_hash(config, target_locale: str) -> str:
    payload = {"target_locale": target_locale}
    for name in ("provider", "base_url", "model", "game_profile", "temperature", "max_tokens"):
        value = getattr(config, name, None)
        if value is not None:
            payload[name] = value
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _llm_config_for_locale(config, target_locale: str | None):
    if target_locale is None or getattr(config, "target_lang", None) == target_locale:
        return config
    cloned = copy.copy(config)
    cloned.target_lang = target_locale
    return cloned


def _cancelled(signal: object | None) -> bool:
    if signal is None:
        return False
    state = getattr(signal, "is_cancelled", None)
    if state is not None:
        return bool(state() if callable(state) else state)
    is_set = getattr(signal, "is_set", None)
    return bool(is_set()) if callable(is_set) else False


def _raise_if_cancelled(signal: object | None) -> None:
    if _cancelled(signal):
        raise TaskCancelled("FOMOD pipeline cancelled")
