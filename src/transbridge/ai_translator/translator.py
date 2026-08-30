"""
自动翻译主控器。

AutoTranslator.translate() 驱动整个翻译流程：
  1. 批次规划
  2. 按轮次依序执行批次翻译（轮次间刷新术语库）
  3. 将结果写回集合（stage=2，表示AI翻译待审核）
  4. 专有名词批次完成后自动写入动态术语库
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import TYPE_CHECKING
import unicodedata

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY
from transbridge.infra.llm_structured_outputs import LlmStructuredOutputTruncatedError


class _CancelledByPause(BaseException):
    """暂停时中断当前 API 调用所用的控制流异常（BaseException 以跳过 except Exception 块）。"""


class _CancelledByStop(BaseException):
    """停止时中断当前 API 调用所用的控制流异常。"""


class _RepetitionDetected(Exception):
    """流式阶段检测到极端重复输出时中断请求。"""

    def __init__(self, entry_id: str | None = None):
        self.entry_id = entry_id


def _select_stage_candidates(candidates: list, *, overwrite: bool) -> list:
    """Apply workflow targeting first, then the shared discrete Stage policy."""
    selected = candidates
    if not overwrite:
        selected = [entry for entry in selected if not entry.translation or entry.stage == 0]
    return [
        entry
        for entry in selected
        if DEFAULT_STAGE_POLICY.allows_ai(entry.stage, entry.translation, original=entry.original)
    ]


def _select_post_process_candidates(entries: list, target_entry_ids: list[str] | None) -> list:
    """Keep post-processing inside the same automatic-edit policy as translation."""
    target_set = None if target_entry_ids is None else set(target_entry_ids)
    return [
        entry
        for entry in entries
        if entry.translation
        and (target_set is None or entry.key in target_set)
        and DEFAULT_STAGE_POLICY.allows_ai(entry.stage, entry.translation, original=entry.original)
    ]


if TYPE_CHECKING:
    from transbridge.ai_translator.batch_planner import Batch
    from transbridge.ai_translator.project_terminology_adapter import ProjectTerminologyAdapter
    from transbridge.application.terminology.effective import TerminologyLookupContext
    from transbridge.application.translation import ReportSnapshot
    from transbridge.application.translation.ai_request_budget import AiRequestBudget
    from transbridge.converter.translation_entry import TranslationEntry
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection
    from transbridge.infra.llm_client import LLMClient
    from transbridge.paratranz.config_manager import LLMConfig


@dataclass
class TranslatorConfig:
    llm_config: LLMConfig
    esp_path: str
    overwrite: bool = False  # True = 全部重翻，False = 仅翻未翻译


@dataclass
class TranslationResult:
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    new_dynamic_terms: int = 0
    failed_entries: list[str] = field(default_factory=list)
    post_process_result: ReportSnapshot | None = None  # canonical 翻译/后处理报告快照
    refine_results: dict | None = None  # {entry_id: RefineResult}，后处理修复中间数据
    polish_results: dict | None = None  # {entry_id: PolishResult}，后处理润色中间数据
    decisions: dict | None = None  # {entry_id: ArbiterDecision}，后处理裁决中间数据
    report_path: str | None = None  # 生成的 Excel 报告路径
    report_paths: tuple[str, ...] = ()  # 同一快照派生的全部报告路径
    report_diagnostics: tuple[str, ...] = ()  # 报告渲染诊断，不混入翻译失败条目


@dataclass
class ProgressCheckpoint:
    """批次级断点，持久化到 data/ai_translator/{esp_stem}/{esp_stem}_progress.json。"""

    esp_stem: str
    target_entry_ids: list[str] | None
    overwrite: bool
    completed_fingerprints: list[list[str]]  # 每项为已完成批次的排序 entry id 列表
    result_so_far: dict  # success_count / failed_count / new_dynamic_terms
    run_id: str = ""
    term_repairs: list[dict] = field(default_factory=list)
    completed_term_repairs: list[str] = field(default_factory=list)
    terminology_snapshot: dict | None = None

    @staticmethod
    def _get_path(esp_path: str) -> str:
        from transbridge.paratranz.config_manager import LLMConfig

        stem = Path(esp_path).stem
        ai_dir = LLMConfig.get_ai_translator_dir(stem)
        return os.path.join(ai_dir, f"{stem}_progress.json")

    def save(self, esp_path: str) -> None:
        path = self._get_path(esp_path)
        data = {
            "esp_stem": self.esp_stem,
            "target_entry_ids": self.target_entry_ids,
            "overwrite": self.overwrite,
            "completed_fingerprints": self.completed_fingerprints,
            "result_so_far": self.result_so_far,
            "run_id": self.run_id,
            "term_repairs": self.term_repairs,
            "completed_term_repairs": self.completed_term_repairs,
            "terminology_snapshot": self.terminology_snapshot,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, esp_path: str) -> ProgressCheckpoint | None:
        path = cls._get_path(esp_path)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return cls(
                esp_stem=data.get("esp_stem", ""),
                target_entry_ids=data.get("target_entry_ids"),
                overwrite=data.get("overwrite", False),
                completed_fingerprints=data.get("completed_fingerprints", []),
                result_so_far=data.get("result_so_far", {}),
                run_id=data.get("run_id", ""),
                term_repairs=data.get("term_repairs", []),
                completed_term_repairs=data.get("completed_term_repairs", []),
                terminology_snapshot=data.get("terminology_snapshot"),
            )
        except Exception:
            return None

    def delete(self, esp_path: str) -> None:
        path = self._get_path(esp_path)
        if os.path.exists(path):
            os.remove(path)


class AutoTranslator:
    def __init__(
        self,
        config: TranslatorConfig,
        paratranz_client=None,
        project_id: int | None = None,
        shared_in_flight_terms: dict | None = None,
        shared_in_flight_lock: threading.Lock | None = None,
        candidate_checkpoint=None,
        run_id_factory: Callable[[], str] | None = None,
        llm_client: LLMClient | None = None,
        term_llm_client: LLMClient | None = None,
        llm_client_wrapper: Callable[[LLMClient], LLMClient] | None = None,
        term_llm_client_wrapper: Callable[[LLMClient], LLMClient] | None = None,
        request_budget: AiRequestBudget | None = None,
        effective_terminology: ProjectTerminologyAdapter | None = None,
        terminology_context: TerminologyLookupContext | None = None,
        legacy_term_filter: object | None = None,
        terminology_snapshot: object | None = None,
    ):
        self._cfg = config
        self._paratranz_client = paratranz_client
        self._project_id = project_id
        self._candidate_checkpoint_port = candidate_checkpoint
        self._terminology_snapshot = terminology_snapshot
        self._run_id_factory = run_id_factory or (lambda: f"translation-{secrets.token_hex(16)}")
        self._candidate_session = None

        from transbridge.ai_translator.batch_planner import BatchPlanner
        from transbridge.ai_translator.prompt_builder import PromptBuilder
        from transbridge.ai_translator.term_database import TermDatabaseManager
        from transbridge.application.translation.ai_request_budget import AiRequestBudget
        from transbridge.infra.llm_client import create_llm_client
        from transbridge.infra.llm_reasoning import ReasoningIntent, with_reasoning_intent

        raw_delegate = llm_client or create_llm_client(config.llm_config)
        delegate = with_reasoning_intent(raw_delegate, config.llm_config, ReasoningIntent.PREFER_DIRECT)
        self._raw_llm = delegate
        self._raw_term_llm = (
            delegate
            if term_llm_client is None or term_llm_client is raw_delegate
            else with_reasoning_intent(term_llm_client, config.llm_config, ReasoningIntent.PREFER_DIRECT)
        )
        self._llm_client_wrapper = llm_client_wrapper
        self._term_llm_client_wrapper = term_llm_client_wrapper
        self._request_budget = request_budget or AiRequestBudget(getattr(config.llm_config, "max_concurrent", 1))
        self._llm = delegate
        self._builder = PromptBuilder(
            game_profile=config.llm_config.game_profile,
            target_lang=config.llm_config.target_lang,
        )
        self._term_mgr = TermDatabaseManager(
            config=config.llm_config,
            esp_path=config.esp_path,
            paratranz_client=paratranz_client,
            project_id=project_id,
            effective_loader=effective_terminology,
            terminology_context=terminology_context,
            legacy_term_filter=legacy_term_filter,
        )
        self._extractor = None
        self._planner = BatchPlanner(
            max_tokens_per_batch=config.llm_config.max_tokens_per_batch,
            model=getattr(config.llm_config, "model", ""),
        )

        # In-flight 术语缓存：并发批次间实时共享的术语
        # key: term (str), value: translation (str)
        # 支持外部注入以实现多插件间共享
        if shared_in_flight_terms is not None and shared_in_flight_lock is not None:
            self._in_flight_terms = shared_in_flight_terms
            self._in_flight_lock = shared_in_flight_lock
            self._owns_in_flight_cache = False
        else:
            self._in_flight_terms: dict[str, str] = {}
            self._in_flight_lock = threading.Lock()
            self._owns_in_flight_cache = True

    def _monitored_chat(
        self,
        messages: list[dict] | None,
        max_tokens: int,
        pause_event: threading.Event | None,
        stop_event: threading.Event | None,
        chunk_callback: Callable[[str], None] | None = None,
        messages_factory: Callable[[], list[dict]] | None = None,
    ) -> str:
        """运行 LLM 调用，期间每 50ms 检查 pause_event / stop_event；触发时立即取消请求。
        chunk_callback: 非 None 时启用流式调用，每收到一个文本块即回调。
        """
        result_holder: list = [None]
        error_holder: list = [None]
        done = threading.Event()

        def _call():
            try:
                if messages_factory is not None and chunk_callback is not None:
                    prepared = getattr(self._llm, "chat_stream_prepared", None)
                    if callable(prepared):
                        result_holder[0] = prepared(messages_factory, max_tokens, chunk_callback)
                    else:
                        result_holder[0] = self._llm.chat_stream(messages_factory(), max_tokens, chunk_callback)
                elif messages_factory is not None:
                    prepared = getattr(self._llm, "chat_prepared", None)
                    if callable(prepared):
                        result_holder[0] = prepared(messages_factory, max_tokens)
                    else:
                        result_holder[0] = self._llm.chat(messages_factory(), max_tokens)
                elif chunk_callback is not None:
                    result_holder[0] = self._llm.chat_stream(messages or [], max_tokens, chunk_callback)
                else:
                    result_holder[0] = self._llm.chat(messages or [], max_tokens)
            except Exception as e:
                error_holder[0] = e
            finally:
                done.set()

        t = threading.Thread(target=_call, daemon=True)
        t.start()

        while not done.wait(timeout=0.05):
            if stop_event is not None and stop_event.is_set():
                self._llm.cancel()
                done.wait(timeout=10)
                raise _CancelledByStop()
            if pause_event is not None and not pause_event.is_set():
                self._llm.cancel()
                done.wait(timeout=10)
                raise _CancelledByPause()

        if error_holder[0] is not None:
            raise error_holder[0]
        return result_holder[0]

    @staticmethod
    def _max_consecutive_repeat(text: str) -> int:
        """计算文本中最大连续重复次数。"""
        if not text:
            return 0
        return max(
            (len(m.group(0)) for m in re.finditer(r"(.|.{2}|.{3})\1*", text)),
            default=0,
        )

    @staticmethod
    def _is_translation_abnormal(original: str, translation: str) -> bool:
        """
        检测单个译文是否异常（重复/回显）。
        先检测译文重复数，超过阈值后再与原文比对，避免误伤原文本身就带重复的正常内容。
        """
        if not translation or not original:
            return False

        # 1. 先检测译文最大连续重复数
        trans_repeat = AutoTranslator._max_consecutive_repeat(translation)

        # 2. 译文重复数未超过阈值，不介入
        if trans_repeat <= 10:
            return False

        # 3. 译文超过阈值，检测原文重复数
        orig_repeat = AutoTranslator._max_consecutive_repeat(original)

        # 4. 译文重复超过原文即判定异常
        if trans_repeat > orig_repeat:
            return True

        return False

    @staticmethod
    def _contains_required_translation(translation: str, required: str) -> bool:
        """Compare a repair constraint after Unicode and whitespace normalization."""

        def _normalized(value: str) -> str:
            return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()

        normalized_required = _normalized(required)
        return bool(normalized_required) and normalized_required in _normalized(translation)

    @staticmethod
    def _wait_until_resumed(
        pause_event: threading.Event | None,
        stop_event: threading.Event,
    ) -> None:
        """Wait for resume without letting a paused worker hide a stop request."""
        if pause_event is None:
            return
        while not pause_event.wait(timeout=0.05):
            if stop_event.is_set():
                raise _CancelledByStop()

    @staticmethod
    def _detect_stream_repetition(buffer: str, max_orig_repeat: int = 0) -> tuple[bool, int]:
        """
        对整个流式 buffer 做重复检测，支持自适应阈值。
        阈值与原文最大重复长度挂钩，避免误伤原文本身就带重复的正常翻译。
        返回 (是否异常, 截断位置)。
        """
        text = "".join(buffer) if isinstance(buffer, list) else buffer
        buffer_repeat = AutoTranslator._max_consecutive_repeat(text)
        adaptive_threshold = max(80, max_orig_repeat * 2 + 20)
        if buffer_repeat > adaptive_threshold:
            for m in re.finditer(r"(.|.{2}|.{3})\1*", text):
                if len(m.group(0)) == buffer_repeat:
                    return True, m.start()
        # 额外兜底：4-10 字符短语的极端重复
        for length, min_count in [(4, 20), (5, 15), (6, 12), (7, 10), (8, 10), (9, 8), (10, 8)]:
            m = re.search(r"(.{" + str(length) + r"})\1{" + str(min_count - 1) + r",}", text)
            if m:
                repeat_total = len(m.group(0))
                if repeat_total > 200 and repeat_total > max_orig_repeat * 1.5 + 20:
                    return True, m.start()
        return False, -1

    def _salvage_from_repetition_buffer(
        self, buffer: str, expected_keys: set[str], max_orig_repeat: int = 0
    ) -> dict[str, str]:
        """截断重复部分，只恢复 results envelope 中已完整闭合的结果项。"""
        from transbridge.ai_translator.prompt_builder import _extract_partial_translation_results

        text = "".join(buffer) if isinstance(buffer, list) else buffer
        is_abnormal, truncate_pos = self._detect_stream_repetition(text, max_orig_repeat=max_orig_repeat)
        if not is_abnormal:
            return {}
        truncated = text[:truncate_pos]
        return {
            entry_id: translation
            for entry_id, translation in _extract_partial_translation_results(truncated).items()
            if entry_id in expected_keys
        }

    def translate(
        self,
        collection: TranslationEntryCollection,
        target_entry_ids: list[str] | None,
        progress_callback: Callable[[int, int, str, int, int, int], None],
        stop_event: threading.Event,
        pause_event: threading.Event | None = None,
        checkpoint: ProgressCheckpoint | None = None,
        log_callback: Callable[[int, str], None] | None = None,
        stream_callback: Callable[[int, str], None] | None = None,
        stage_progress_callback: Callable[[str, int, int, str], None] | None = None,
    ) -> TranslationResult:
        """
        progress_callback(current, total, message, success_count, failed_count, new_terms)
        log_callback(batch_idx, line)  — batch_idx=-1 为轮次级消息，>=0 为批次专属消息
        stream_callback(chunk) — LLM 流式响应片段回调，可为 None
        stage_progress_callback(stage, current, total, message) — 翻译前准备阶段进度
        pause_event: set=运行中，clear=暂停中（wait() 会阻塞直到 set）
        checkpoint: 传入则从断点继续，跳过已完成批次
        """
        result = TranslationResult()
        lock = threading.Lock()
        t_total_start = time.perf_counter()

        # One run owns one request budget.  Logging wrappers sit outside the
        # limiter so queued calls still receive stable call identities while
        # only admitted calls reach the provider.
        from transbridge.ai_translator.noun_extractor import NounExtractor
        from transbridge.infra.limited_llm_client import LimitedLLMClient

        def _limited(client: LLMClient) -> LLMClient:
            if isinstance(client, LimitedLLMClient):
                if client.budget is not self._request_budget:
                    raise ValueError("pre-limited LLM client belongs to a different AI request budget")
                return client
            return LimitedLLMClient(
                client,
                self._request_budget,
                cancel_event=stop_event,
                pause_event=pause_event,
            )

        translation_client = _limited(self._raw_llm)
        if self._llm_client_wrapper is not None:
            translation_client = self._llm_client_wrapper(translation_client)
        self._llm = translation_client
        if self._raw_term_llm is self._raw_llm and self._term_llm_client_wrapper is None:
            term_client = translation_client
        else:
            term_client = _limited(self._raw_term_llm)
            if self._term_llm_client_wrapper is not None:
                term_client = self._term_llm_client_wrapper(term_client)
        self._extractor = NounExtractor(
            term_client,
            self._builder,
            max_output_tokens=self._cfg.llm_config.max_output_tokens,
        )

        # 清空 in-flight 缓存（新翻译会话）
        # 仅当缓存是本实例拥有时才清空，批量翻译时使用共享缓存不应清空
        if self._owns_in_flight_cache:
            with self._in_flight_lock:
                self._in_flight_terms.clear()

        # 从断点恢复累计统计
        completed_fps: set[frozenset] = set()
        if checkpoint and checkpoint.run_id:
            self._validate_checkpoint_terminology(checkpoint)
            result.success_count = checkpoint.result_so_far.get("success_count", 0)
            result.failed_count = checkpoint.result_so_far.get("failed_count", 0)
            result.new_dynamic_terms = checkpoint.result_so_far.get("new_dynamic_terms", 0)
            completed_fps = {frozenset(fp) for fp in checkpoint.completed_fingerprints}

        # 筛选待翻译词条
        all_entries = list(collection)
        if target_entry_ids is not None:
            id_set = set(target_entry_ids)
            candidates = [e for e in all_entries if e.key in id_set]
        else:
            candidates = all_entries

        candidates = _select_stage_candidates(candidates, overwrite=self._cfg.overwrite)

        if not candidates:
            return result

        from transbridge.application.translation import (
            FilesystemTranslationCheckpointPort,
            LegacyTranslationCandidateSession,
            TranslationInput,
        )
        from transbridge.application.translation.workload_models import (
            canonical_hash,
            translation_input_fingerprint,
        )

        run_id = checkpoint.run_id if checkpoint and checkpoint.run_id else self._run_id_factory()
        translation_inputs = tuple(
            TranslationInput(
                entry.identity,
                entry.revision,
                entry.original,
                entry.translation,
                entry.stage,
                entry.context or "",
                _terminology_plugin_id(self._term_mgr, entry),
            )
            for entry in candidates
        )
        self._candidate_session = None

        max_workers = self._cfg.llm_config.max_concurrent

        # Request concurrency controls admission only; it never changes batch boundaries.
        plan = self._planner.plan(candidates)
        if plan.oversized:
            details = "；".join(item.message for item in plan.oversized[:5])
            remaining = len(plan.oversized) - 5
            suffix = f"；另有 {remaining} 条" if remaining > 0 else ""
            raise ValueError(f"存在超过每请求内容 Token 上限的条目：{details}{suffix}")
        all_batches = plan.all_batches()
        total_batches = len(all_batches)
        total_entries = sum(len(b.entries) for b in all_batches)

        # 断点续传：将已完成的词条数加到总计中，避免 current 超过 total
        completed_from_checkpoint = 0
        if checkpoint and checkpoint.run_id:
            completed_from_checkpoint = checkpoint.result_so_far.get("success_count", 0) + checkpoint.result_so_far.get(
                "failed_count", 0
            )
            total_entries += completed_from_checkpoint

        if total_batches == 0:
            return result

        esp_stem = Path(self._cfg.esp_path).stem
        # 全局批次计数器（并发时需加锁）
        batch_counter = [0]

        def _emit(msg: str):
            with lock:
                done_entries = result.success_count + result.failed_count
                pct = done_entries * 100 // total_entries if total_entries > 0 else 0
                full_msg = f"[{pct:3d}%] {done_entries}/{total_entries} | {msg}"
                progress_callback(
                    done_entries,
                    total_entries,
                    full_msg,
                    result.success_count,
                    result.failed_count,
                    result.new_dynamic_terms,
                )

        def _log(line: str, idx: int = -1):
            if log_callback:
                log_callback(idx, line)

        # 主动加载术语库并记录各来源情况
        self._term_mgr.load_all()
        for source, count, err in self._term_mgr.get_load_log():
            if err:
                _log(f"⚠ 术语来源 [{source}] 加载失败: {err}")
            else:
                _log(f"  术语来源 [{source}]: {count} 条")

        conflict_records = ()
        if getattr(self._cfg.llm_config, "retrieval_enabled", True):
            from transbridge.ai_translator.existing_term_extractor import (
                ExistingTermSeeder,
                should_seed_existing_terms,
            )

            if should_seed_existing_terms(all_entries, candidates):
                term_progress = [0, 1]

                def _term_progress(current: int, total: int, message: str) -> None:
                    term_progress[:] = [current, max(1, total)]
                    if stage_progress_callback is not None:
                        stage_progress_callback("terms", current, max(1, total), message)

                try:
                    seed_result = ExistingTermSeeder(
                        self._term_mgr,
                        self._extractor,
                        max_tokens_per_batch=self._cfg.llm_config.max_tokens_per_batch,
                        model=self._cfg.llm_config.model,
                        max_concurrent=max_workers,
                    ).seed(
                        all_entries,
                        progress_callback=_term_progress,
                        stop_event=stop_event,
                        pause_event=pause_event,
                    )
                except Exception as exc:
                    message = f"术语抽取失败，已跳过：{exc}"
                    _term_progress(term_progress[1], term_progress[1], message)
                    _log(f"⚠ 存量译文术语初始化失败，继续使用已有术语库: {exc}")
                else:
                    if seed_result.cancelled:
                        _log("⏹ 存量译文术语初始化已停止")
                        return result
                    result.new_dynamic_terms += seed_result.added_count
                    if seed_result.error:
                        failure_message = f"术语抽取失败，已停止后续批次：{seed_result.error}"
                        _log(f"⚠ {failure_message}；继续使用名称术语和已有术语库")
                    message = (
                        "  存量译文术语: "
                        f"名称 {seed_result.direct_added}，"
                        f"文本 {seed_result.text_added}，"
                        f"冲突 {seed_result.conflicts}，"
                        f"已有 {seed_result.skipped_existing}"
                    )
                    _log(message)
                    conflict_records = seed_result.conflict_records
            elif stage_progress_callback is not None:
                stage_progress_callback("terms", 1, 1, "无需从已有译文初始化术语")

        from transbridge.application.io import StagePolicy

        stage_policy = StagePolicy()
        entries_by_identity = {entry.identity: entry for entry in all_entries}
        repair_requirements: dict[EntryKey, dict[str, str]] = {}
        repair_entries_by_key: dict[EntryKey, TranslationEntry] = {}
        restored_repair_keys: set[EntryKey] = set()
        completed_term_repairs = set(checkpoint.completed_term_repairs if checkpoint else ())

        # Text extraction writes a completion marker, so a resumed run may not
        # regenerate conflict evidence.  Persisted repair jobs keep that queue
        # recoverable until the guarded candidate commit finishes.
        for payload in checkpoint.term_repairs if checkpoint else ():
            try:
                entry_key = EntryKey.from_dict(payload["entry_key"])
                required = {
                    str(term): str(translation)
                    for term, translation in dict(payload["required_terms"]).items()
                    if str(term).strip() and str(translation).strip()
                }
            except (KeyError, TypeError, ValueError):
                _log("⚠ 断点中的术语冲突重翻任务无效，已跳过")
                continue
            entry = entries_by_identity.get(entry_key)
            if entry is None or not required:
                continue
            if not stage_policy.allows_ai(entry.stage, entry.translation, original=entry.original):
                _log(f"⚠ 断点中的术语冲突条目不可由 AI 修改，已跳过：{entry.key}")
                continue
            repair_entries_by_key[entry_key] = entry
            repair_requirements[entry_key] = required
            restored_repair_keys.add(entry_key)

        for conflict in conflict_records:
            if conflict.kind != "effective_library":
                continue
            entry = entries_by_identity.get(conflict.entry_key)
            if entry is None:
                _log(f"⚠ 术语冲突条目已不存在，跳过重翻：{conflict.entry_key.local_key}")
                continue
            if not stage_policy.allows_ai(entry.stage, entry.translation, original=entry.original):
                _log(f"⚠ 术语冲突条目不可由 AI 修改，跳过重翻：{entry.key}")
                continue
            repair_entries_by_key[entry.identity] = entry
            repair_requirements.setdefault(entry.identity, {})[conflict.term] = conflict.canonical_translation

        # Refresh the checkpoint fingerprint inputs from the currently effective
        # library.  A changed authority invalidates an already completed repair.
        fingerprint_repair_requirements: dict[EntryKey, dict[str, str]] = {}
        for entry_key, required in repair_requirements.items():
            refreshed: dict[str, str] = {}
            repair_entry = repair_entries_by_key[entry_key]
            context_resolver = getattr(self._term_mgr, "lookup_context_for_entry", None)
            lookup_context = context_resolver(repair_entry) if callable(context_resolver) else None
            for term, translation in required.items():
                canonical = (
                    self._term_mgr.resolve_term(term, lookup_context)
                    if lookup_context is not None
                    else self._term_mgr.resolve_term(term)
                )
                if canonical is None or not canonical.translation.strip():
                    refreshed[term] = translation
                else:
                    refreshed[canonical.term] = canonical.translation
            if entry_key in restored_repair_keys and refreshed != required:
                completed_term_repairs.discard(entry_key.serialize())
            fingerprint_repair_requirements[entry_key] = refreshed

        all_repair_entries = [repair_entries_by_key[key] for key in sorted(repair_entries_by_key)]
        repair_entries = [
            entry for entry in all_repair_entries if entry.identity.serialize() not in completed_term_repairs
        ]
        if repair_entries:
            _log(f"  术语冲突重翻队列：{len(repair_entries)} 条")
            for entry in repair_entries:
                differences = "，".join(
                    f"{term}→{translation}" for term, translation in repair_requirements[entry.identity].items()
                )
                _log(f"  [术语冲突入队] {entry.key}: {differences}")
        # In overwrite mode a conflicting translated entry may already be in
        # the normal plan.  The one-entry repair queue replaces that normal
        # occurrence so one CandidateSet never contains duplicate EntryKeys.
        repair_identities = {entry.identity for entry in all_repair_entries}
        candidates = [entry for entry in candidates if entry.identity not in repair_identities]
        plan = self._planner.plan(candidates)
        all_batches = plan.all_batches()
        total_batches = len(all_batches)
        total_entries = (
            sum(len(batch.entries) for batch in all_batches) + completed_from_checkpoint + len(repair_entries)
        )
        scoped_entries = [*candidates, *all_repair_entries]
        translation_inputs = tuple(
            TranslationInput(
                entry.identity,
                entry.revision,
                entry.original,
                entry.translation,
                entry.stage,
                entry.context or "",
                _terminology_plugin_id(self._term_mgr, entry),
            )
            for entry in scoped_entries
        )

        spec_fingerprint = canonical_hash({
            "run_id": run_id,
            "config_revision": self._cfg.llm_config.config_revision,
            "provider": self._cfg.llm_config.provider,
            "base_url": self._cfg.llm_config.base_url,
            "model": self._cfg.llm_config.model,
            "target_lang": self._cfg.llm_config.target_lang,
            "scope": [entry.entry_key.to_dict() for entry in translation_inputs],
            "term_repairs": [
                {
                    "entry_key": entry_key.to_dict(),
                    "required_terms": sorted(fingerprint_repair_requirements[entry_key].items()),
                }
                for entry_key in sorted(repair_requirements)
            ],
        })
        candidate_port = self._candidate_checkpoint_port
        if candidate_port is None:
            checkpoint_root = (
                Path(self._cfg.llm_config.get_ai_translator_dir(Path(self._cfg.esp_path).stem)) / "runtime-v2"
            )
            candidate_port = FilesystemTranslationCheckpointPort(checkpoint_root)
        self._candidate_session = LegacyTranslationCandidateSession(
            run_id=run_id,
            owner_id="legacy-auto-translator",
            spec_fingerprint=spec_fingerprint,
            input_fingerprint=translation_input_fingerprint(translation_inputs),
            checkpoint=candidate_port,
            provider=self._cfg.llm_config.provider,
            model=self._cfg.llm_config.model,
        )

        term_repairs_checkpoint = [
            {
                "entry_key": entry_key.to_dict(),
                "required_terms": dict(sorted(repair_requirements[entry_key].items())),
            }
            for entry_key in sorted(repair_requirements)
        ]
        checkpoint_write_lock = threading.Lock()

        def _save_checkpoint():
            with checkpoint_write_lock:
                with lock:
                    cp = ProgressCheckpoint(
                        esp_stem=esp_stem,
                        target_entry_ids=target_entry_ids,
                        overwrite=self._cfg.overwrite,
                        completed_fingerprints=[sorted(fp) for fp in completed_fps],
                        result_so_far={
                            "success_count": result.success_count,
                            "failed_count": result.failed_count,
                            "new_dynamic_terms": result.new_dynamic_terms,
                        },
                        run_id=run_id,
                        term_repairs=term_repairs_checkpoint,
                        completed_term_repairs=sorted(completed_term_repairs),
                        terminology_snapshot=(
                            None if self._terminology_snapshot is None else self._terminology_snapshot.to_dict()
                        ),
                    )
                cp.save(self._cfg.esp_path)

        if term_repairs_checkpoint:
            _save_checkpoint()

        def _run_one_batch(batch: Batch, round_name: str) -> None:
            """在线程池中执行单个批次，含暂停/停止检查。"""
            with lock:
                batch_counter[0] += 1
                idx = batch_counter[0]
            batch_fp = frozenset(e.key for e in batch.entries)

            # 跳过已完成批次（断点续传）
            with lock:
                already_done = batch_fp in completed_fps
            if already_done:
                msg = f"{round_name} | {batch.batch_type} [已跳过]"
                _emit(msg)
                return

            # 暂停检查
            if pause_event is not None:
                pause_event.wait()

            if stop_event.is_set():
                _save_checkpoint()
                return

            msg = f"{round_name} | {batch.batch_type}（{len(batch.entries)} 条）"
            _emit(msg)

            # 批次专属 log 回调（携带 idx）
            _batch_log_cb = (lambda line: log_callback(idx, line)) if log_callback else None

            def _blog(line: str):
                if _batch_log_cb:
                    _batch_log_cb(line)

            # 批次专属 stream 回调（将 idx 绑定到外层 stream_callback）
            _per_batch_stream = (lambda chunk: stream_callback(idx, chunk)) if stream_callback else None

            # 批次头
            _blog("\n开始翻译：")
            _blog(f"任务{idx}：{batch.batch_type}")
            _blog("-----------------------")

            t_batch = time.perf_counter()
            _batch_timing: dict = {}
            with lock:
                _success_before = result.success_count
                _terms_before = result.new_dynamic_terms

            def _progress_emit() -> None:
                _emit(msg)

            try:
                self._run_batch(
                    batch,
                    collection,
                    result,
                    lock,
                    _batch_log_cb,
                    pause_event,
                    stop_event,
                    _per_batch_stream,
                    _timing_out=_batch_timing,
                    progress_emit=_progress_emit,
                )
            except _CancelledByStop:
                _blog("⏹ 批次已中断（停止）")
                _save_checkpoint()
                return
            except _CancelledByPause:
                _blog("⏸ 批次已中断（暂停）")
                if pause_event is not None:
                    pause_event.wait()  # 阻塞直到用户点击继续
                if stop_event.is_set():
                    _save_checkpoint()
                    return
                # 继续后重试本批次（不重新计数，沿用原 idx）
                _emit(f"{round_name} | {batch.batch_type}（{len(batch.entries)} 条，重试）")
                _batch_timing.clear()
                with lock:
                    _success_before = result.success_count
                    _terms_before = result.new_dynamic_terms
                self._run_batch(
                    batch,
                    collection,
                    result,
                    lock,
                    _batch_log_cb,
                    pause_event,
                    stop_event,
                    _per_batch_stream,
                    _timing_out=_batch_timing,
                    progress_emit=_progress_emit,
                )

            t_batch_elapsed = time.perf_counter() - t_batch
            with lock:
                _success_delta = result.success_count - _success_before
                _terms_delta = result.new_dynamic_terms - _terms_before

            # 批次尾
            _blog("-----------------------")
            _blog("已完成：")
            _blog(f"术语匹配时长:{_batch_timing.get('t_terms', 0):.2f} s")
            _blog(f"LLM调用时长:{_batch_timing.get('t_llm', 0):.2f} s")
            _blog(f"解析时长：{_batch_timing.get('t_parse', 0):.3f} s")
            _blog(f"总时长：{t_batch_elapsed:.2f} s")
            _blog(f"翻译词条数：{_success_delta}")
            _blog(f"新增术语数：{_terms_delta}")

            with lock:
                completed_fps.add(batch_fp)
            _save_checkpoint()
            _emit(msg)

        # ── 权威术语冲突：单条重翻后再进入正常三轮 ─────────────────────────────
        if repair_entries and not stop_event.is_set():
            repair_batches: list[tuple[Batch, dict[str, dict[str, str]]]] = []
            for entry in repair_entries:
                repair_plan = self._planner.plan([entry])
                if repair_plan.oversized:
                    message = repair_plan.oversized[0].message
                    _log(f"⚠ 术语冲突重翻已跳过：{message}")
                    with lock:
                        result.failed_entries.append(f"{entry.id}: 术语冲突重翻失败：{message}")
                        result.failed_count += 1
                        completed_term_repairs.add(entry.identity.serialize())
                    _save_checkpoint()
                    continue
                batches = repair_plan.all_batches()
                if not batches:
                    with lock:
                        result.failed_entries.append(f"{entry.id}: 术语冲突重翻失败：无法生成请求批次")
                        result.failed_count += 1
                        completed_term_repairs.add(entry.identity.serialize())
                    _save_checkpoint()
                    continue
                repair_batches.append((batches[0], {entry.key: repair_requirements[entry.identity]}))

            def _run_one_term_repair(batch: Batch, required: dict[str, dict[str, str]]) -> None:
                if stop_event.is_set():
                    return
                self._wait_until_resumed(pause_event, stop_event)
                if stop_event.is_set():
                    return

                entry = batch.entries[0]
                with lock:
                    batch_counter[0] += 1
                    idx = batch_counter[0]
                    success_before = result.success_count
                    failed_before = result.failed_count
                _emit(f"术语冲突重翻 | {entry.key}")
                _batch_log_cb = (lambda line: log_callback(idx, line)) if log_callback else None
                _per_batch_stream = (lambda chunk: stream_callback(idx, chunk)) if stream_callback else None
                if _batch_log_cb:
                    _batch_log_cb("\n开始术语冲突重翻：")
                    _batch_log_cb(f"条目：{entry.key}")
                    _batch_log_cb(
                        "权威术语：" + "，".join(f"{term}→{target}" for term, target in required[entry.key].items())
                    )
                    _batch_log_cb("-----------------------")
                retrying = False
                while True:
                    try:
                        success = self._run_batch(
                            batch,
                            collection,
                            result,
                            lock,
                            _batch_log_cb,
                            pause_event,
                            stop_event,
                            _per_batch_stream,
                            _min_size=1,
                            progress_emit=lambda: _emit(
                                f"术语冲突重翻 | {entry.key}" + ("（重试）" if retrying else "")
                            ),
                            required_terms_by_entry=required,
                            update_terms=False,
                        )
                        break
                    except _CancelledByStop:
                        if _batch_log_cb:
                            _batch_log_cb("⏹ 术语冲突重翻已中断（停止）")
                        _save_checkpoint()
                        return
                    except _CancelledByPause:
                        if _batch_log_cb:
                            _batch_log_cb("⏸ 术语冲突重翻已中断（暂停）")
                        try:
                            self._wait_until_resumed(pause_event, stop_event)
                        except _CancelledByStop:
                            _save_checkpoint()
                            return
                        retrying = True
                    except Exception as exc:
                        if _batch_log_cb:
                            _batch_log_cb(f"⚠ 术语冲突重翻请求失败：{type(exc).__name__}: {exc}")
                        with lock:
                            result.failed_entries.append(f"{entry.id}: 术语冲突重翻失败：{type(exc).__name__}: {exc}")
                            result.failed_count += 1
                        success = 0
                        break
                with lock:
                    failed_delta = result.failed_count - failed_before
                    success_delta = result.success_count - success_before
                    if success <= 0 and success_delta <= 0 and failed_delta <= 0:
                        result.failed_entries.append(f"{entry.id}: 术语冲突重翻失败：模型未返回有效译文")
                        result.failed_count += 1
                if _batch_log_cb:
                    _batch_log_cb("-----------------------")
                    _batch_log_cb("术语冲突重翻成功" if success > 0 else "术语冲突重翻失败，保留原译文")
                with lock:
                    completed_term_repairs.add(entry.identity.serialize())
                _save_checkpoint()
                _emit(f"术语冲突重翻 | {entry.key}")

            if repair_batches:
                _log(f"\n── 术语冲突重翻开始（{len(repair_batches)} 条）──")
                with lock:
                    repair_success_before = result.success_count
                    repair_failed_before = result.failed_count
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [
                        executor.submit(_run_one_term_repair, batch, required) for batch, required in repair_batches
                    ]
                    for future in as_completed(futures):
                        future.result()
                with lock:
                    repair_succeeded = result.success_count - repair_success_before
                    repair_failed = result.failed_count - repair_failed_before
                _log(f"── 术语冲突重翻完成：成功 {repair_succeeded}，失败 {repair_failed} ──\n")

        # ── 第一轮：所有批次并发 ──────────────────────────────────────────────
        if plan.round1 and not stop_event.is_set():
            t_round = time.perf_counter()
            _log(f"\n── 第一轮开始（{len(plan.round1)} 批，专有名词）──")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_run_one_batch, batch, "第一轮") for batch in plan.round1]
                for f in as_completed(futures):
                    f.result()  # 传播异常
            _log(f"── 第一轮完成: {time.perf_counter() - t_round:.2f}s ──\n")

        # ── 第二轮：quest 间并发，quest 内串行 ───────────────────────────────
        if plan.round2 and not stop_event.is_set():
            t_round = time.perf_counter()
            _log(f"\n── 第二轮开始（{len(plan.round2)} 批，对话）──")

            def _run_quest_group(quest_batches: list[Batch]) -> None:
                for batch in quest_batches:
                    if stop_event.is_set():
                        break
                    _run_one_batch(batch, "第二轮")

            # 过滤已完成批次，基于剩余批次决定并发策略
            pending_round2 = [b for b in plan.round2 if frozenset(e.key for e in b.entries) not in completed_fps]

            if not pending_round2:
                _log("── 第二轮无待处理批次 ──\n")
            else:
                # 按 quest 分组
                pending_quest_groups: dict[str, list[Batch]] = {}
                for batch in pending_round2:
                    pending_quest_groups.setdefault(batch.quest_formid or "", []).append(batch)

                # Quest groups run concurrently, but batches in one quest always
                # retain their original serial order (ADR-003).
                _log(
                    f"  活跃 quest 数={len(pending_quest_groups)}, "
                    f"max_workers={max_workers}, 未完成任务数={len(pending_round2)}, "
                    "并发策略=quest组级（quest内串行）"
                )
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(_run_quest_group, batches) for batches in pending_quest_groups.values()]
                    for f in as_completed(futures):
                        f.result()
                _log(f"── 第二轮完成: {time.perf_counter() - t_round:.2f}s ──\n")

        # ── 第三轮：所有批次并发 ──────────────────────────────────────────────
        if plan.round3 and not stop_event.is_set():
            t_round = time.perf_counter()
            _log(f"\n── 第三轮开始（{len(plan.round3)} 批，长文本）──")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_run_one_batch, batch, "第三轮") for batch in plan.round3]
                for f in as_completed(futures):
                    f.result()
            _log(f"── 第三轮完成: {time.perf_counter() - t_round:.2f}s ──\n")

        t_total = time.perf_counter() - t_total_start

        if not stop_event.is_set() and self._candidate_session is not None:
            from transbridge.application.contracts import OperationOutcome, RequestContext
            from transbridge.application.io.publish import ImmediateCommitGuard

            accepted_count = result.success_count
            commit = self._candidate_session.commit(
                collection,
                RequestContext(
                    "legacy-auto-translator",
                    run_id=run_id,
                    permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
                ),
                ImmediateCommitGuard(run_id, active=lambda: not stop_event.is_set()),
            )
            if commit.outcome in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
                result.success_count = commit.counts.succeeded + commit.counts.skipped
                result.failed_count += commit.counts.failed
            else:
                result.success_count = 0
                result.failed_count += accepted_count
            result.failed_entries.extend(
                f"{diagnostic.code}: {diagnostic.message}" for diagnostic in commit.diagnostics
            )

        report_scope_ids = {entry.entry_key.local_key for entry in translation_inputs}
        for completed_batch in completed_fps:
            report_scope_ids.update(completed_batch)
        report_entries = _select_post_process_candidates(list(collection), sorted(report_scope_ids))

        # ── 后处理：质量检查 ─────────────────────────────────────────────────────
        if (
            not stop_event.is_set()
            and result.success_count > 0
            and getattr(self._cfg.llm_config, "enable_post_process", True)
        ):
            _log("\n── 开始质量检查 ──")
            from transbridge.application.contracts import OperationOutcome, RequestContext
            from transbridge.application.io import StagePolicy
            from transbridge.application.io.publish import ImmediateCommitGuard
            from transbridge.application.translation import (
                CheckerStage,
                FilesystemPostProcessCheckpointPort,
                FilesystemTranslationCheckpointPort,
                LlmClientPostProcessPort,
                LlmPostProcessStage,
                PostProcessExecutionService,
                PostProcessLlmPhase,
                PostProcessWorkload,
                ProofreadStage,
                TranslationInput,
            )
            from transbridge.application.translation.ai_execution_profile import normalize_postprocess_strategy

            from .post_processor import PostProcessor, PostProcessorConfig

            # 只对成功翻译且仍符合自动编辑策略的条目进行后处理。
            entries_to_check = report_entries

            if entries_to_check:
                stages = []
                stage_names = []
                strategy = normalize_postprocess_strategy(getattr(self._cfg.llm_config, "pp_strategy", "proofread"))
                if strategy == "proofread":

                    def resolve_terms(candidate):
                        if self._term_mgr is None or not candidate.original:
                            return {}
                        contextual = getattr(self._term_mgr, "match_terms_for_entry", None)
                        if callable(contextual):
                            return contextual(candidate)
                        return self._term_mgr.match_terms(
                            [candidate.original],
                            context=self._term_mgr.lookup_context_for_entry(candidate),
                        )

                    stages.append(
                        ProofreadStage(
                            self._llm,
                            term_resolver=resolve_terms,
                            target_locale=self._cfg.llm_config.target_lang,
                            game_profile=self._cfg.llm_config.game_profile,
                            polish_level=self._cfg.llm_config.pp_polish_level,
                            model=self._cfg.llm_config.model,
                            max_tokens_per_batch=self._cfg.llm_config.max_tokens_per_batch,
                            refinement_batch_size=max(
                                1,
                                int(getattr(self._cfg.llm_config, "pp_refinement_batch_size", 5)),
                            ),
                            max_output_tokens=self._cfg.llm_config.max_output_tokens,
                            max_workers=self._cfg.llm_config.max_concurrent,
                        )
                    )
                    stage_names.append("proofread")
                else:
                    # 从 LLMConfig 加载严格多阶段后处理配置。
                    pp_config = PostProcessorConfig.from_llm_config(self._cfg.llm_config)
                    if not bool(self._llm):
                        pp_config.enable_quality_gate = False
                    post_processor = PostProcessor(pp_config)
                    post_processor.register_default_checkers(
                        term_manager=self._term_mgr,
                        llm_client=self._llm,
                    )
                    checker_phases = {
                        "ConsistencyChecker": "consistency",
                        "FormatValidator": "format",
                        "QualityGateChecker": "quality_gate",
                    }
                    for checker in post_processor._checkers:
                        phase = checker_phases.get(type(checker).__name__)
                        if phase is not None:
                            checker_options = (
                                {
                                    "model": self._cfg.llm_config.model,
                                    "max_tokens_per_batch": self._cfg.llm_config.max_tokens_per_batch,
                                }
                                if phase == "quality_gate"
                                else {}
                            )
                            stages.append(CheckerStage(phase, checker, **checker_options))
                            stage_names.append(phase)
                    llm_port = LlmClientPostProcessPort(
                        self._llm,
                        max_output_tokens=self._cfg.llm_config.max_output_tokens,
                    )
                    llm_phases = (
                        (
                            pp_config.enable_refinement,
                            PostProcessLlmPhase.REFINE,
                            "refinement",
                            pp_config.refinement_batch_size,
                        ),
                        (pp_config.enable_polish, PostProcessLlmPhase.POLISH, "polish", pp_config.polish_batch_size),
                        (
                            pp_config.enable_llm_arbitration,
                            PostProcessLlmPhase.ARBITRATE,
                            "arbitration",
                            pp_config.arbitration_batch_size,
                        ),
                    )
                    for enabled, phase, phase_name, max_items in llm_phases:
                        if enabled:
                            stages.append(
                                LlmPostProcessStage(
                                    phase,
                                    llm_port,
                                    target_locale=self._cfg.llm_config.target_lang,
                                    game_profile=self._cfg.llm_config.game_profile,
                                    base_url=self._cfg.llm_config.base_url,
                                    model=self._cfg.llm_config.model,
                                    max_tokens_per_batch=self._cfg.llm_config.max_tokens_per_batch,
                                    max_items=max_items,
                                )
                            )
                            stage_names.append(phase_name)
                checkpoint_root = Path(self._cfg.esp_path).parent / ".transbridge" / "checkpoints"
                workload = PostProcessWorkload(
                    tuple(stages),
                    stage_policy=StagePolicy(),
                    stage_names=tuple(stage_names),
                    checkpoint_port=FilesystemPostProcessCheckpointPort(checkpoint_root / "postprocess"),
                )
                post_run_id = run_id
                inputs = tuple(
                    TranslationInput(
                        entry.identity,
                        entry.revision,
                        entry.original,
                        entry.translation,
                        entry.stage,
                        entry.context or "",
                        _terminology_plugin_id(self._term_mgr, entry),
                    )
                    for entry in entries_to_check
                )
                execution = PostProcessExecutionService(workload).execute(
                    run_id=post_run_id,
                    entries=inputs,
                    collection=collection,
                    context=RequestContext(
                        "legacy-auto-translator",
                        run_id=post_run_id,
                        permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
                    ),
                    commit_guard=ImmediateCommitGuard(post_run_id, active=lambda: not stop_event.is_set()),
                    commit_checkpoint=FilesystemTranslationCheckpointPort(checkpoint_root / "postprocess-commit"),
                    is_cancelled=stop_event.is_set,
                    run_spec_summary={
                        "source": "auto-translator",
                        "model": self._cfg.llm_config.model,
                    },
                )
                pp_result = execution.report_result.value
                result.post_process_result = execution.report_snapshot
                if pp_result is None:
                    codes = ", ".join(item.code for item in execution.report_result.diagnostics)
                    result.failed_entries.append(f"后处理失败: {codes or 'POSTPROCESS_FAILED'}")
                else:
                    _log(f"质量检查完成：检查 {pp_result.input_count} 条")
                    _log(f"  发现问题：{pp_result.issue_count} 个")
                    if execution.commit_result is not None and execution.commit_result.outcome not in {
                        OperationOutcome.COMPLETED,
                        OperationOutcome.PARTIAL,
                    }:
                        result.failed_entries.extend(
                            f"{diagnostic.code}: {diagnostic.message}"
                            for diagnostic in execution.commit_result.diagnostics
                        )
            else:
                _log("无可检查的条目")

        elif not stop_event.is_set() and result.success_count > 0:
            _log("\n── 质量检查已跳过（用户设置）──")

        from transbridge.application.translation import build_translation_report_snapshot

        result.post_process_result = build_translation_report_snapshot(
            result,
            report_entries,
            run_id=run_id,
            cancelled=stop_event.is_set(),
            before_text_by_key={entry.entry_key: entry.translation for entry in translation_inputs},
        )

        if not stop_event.is_set():
            # 翻译与后处理均完成，删除翻译断点
            ProgressCheckpoint(esp_stem, target_entry_ids, self._cfg.overwrite, [], {}).delete(self._cfg.esp_path)
        elif stop_event.is_set():
            # 被中断时保存翻译断点（若后处理被中断，翻译断点也保留）
            _save_checkpoint()

        _log(f"\n总耗时: {t_total:.2f}s")
        return result

    def _validate_checkpoint_terminology(self, checkpoint: ProgressCheckpoint) -> None:
        payload = checkpoint.terminology_snapshot
        if self._terminology_snapshot is None:
            if payload is not None:
                raise ValueError("翻译断点要求项目术语快照，但当前运行没有该 Project/Variant")
            return
        if payload is None:
            raise ValueError("翻译断点缺少项目术语快照身份，不能安全恢复")
        from transbridge.application.translation.terminology_run_snapshot import TerminologyRunSnapshotRef

        try:
            checkpoint_ref = TerminologyRunSnapshotRef.from_dict(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("翻译断点中的项目术语快照身份无效") from exc
        if checkpoint_ref != self._terminology_snapshot:
            raise ValueError("翻译断点的项目术语版本或内容摘要与当前运行不一致")

    def _run_batch(
        self,
        batch: Batch,
        collection: TranslationEntryCollection,
        result: TranslationResult,
        lock: threading.Lock,
        log_callback: Callable[[str], None] | None = None,
        pause_event: threading.Event | None = None,
        stop_event: threading.Event | None = None,
        stream_callback: Callable[[str], None] | None = None,
        _min_size: int = 1,
        _timing_out: dict | None = None,
        progress_emit: Callable[[], None] | None = None,
        required_terms_by_entry: dict[str, dict[str, str]] | None = None,
        update_terms: bool = True,
    ) -> int:
        from transbridge.converter.context_categories import AUTO_TERM_CONTEXTS

        def _log(line: str):
            if log_callback:
                log_callback(line)

        entries = batch.entries
        if not entries:
            return 0

        t0 = time.perf_counter()
        repair_mode = bool(required_terms_by_entry)
        # ── 精确匹配：原文与术语完全相同的条目直接填充，无需发送给 LLM ──────
        # Conflict repair deliberately goes through the model so every required
        # term can be validated as one atomic single-entry response.
        direct_fill: dict[str, str] = {}  # entry_id → translation
        llm_entries = []
        key_map_all = {e.key: e for e in entries}
        exact_by_entry: dict[str, str] = {}
        if not repair_mode:
            context_resolver = getattr(self._term_mgr, "lookup_context_for_entry", None)
            grouped: dict[object, list[TranslationEntry]] = {}
            for entry in entries:
                context = context_resolver(entry) if callable(context_resolver) else None
                grouped.setdefault(context, []).append(entry)
            for context, contextual_entries in grouped.items():
                originals = [entry.original for entry in contextual_entries]
                exact_matches = (
                    self._term_mgr.exact_match(originals, context=context)
                    if callable(context_resolver)
                    else self._term_mgr.exact_match(originals)
                )
                for entry in contextual_entries:
                    if entry.original in exact_matches:
                        exact_by_entry[entry.key] = exact_matches[entry.original]
        for e in entries:
            if e.key in exact_by_entry:
                direct_fill[e.key] = exact_by_entry[e.key]
            else:
                llm_entries.append(e)

        direct_success = 0
        if direct_fill:
            _log(f"  术语精确匹配: {len(direct_fill)} 条直接填充")
            for eid, trans in direct_fill.items():
                orig = key_map_all[eid].original
                orig_disp = orig[:60] + "…" if len(orig) > 60 else orig
                trans_disp = trans[:60] + "…" if len(trans) > 60 else trans
                _log(f"{orig_disp} -> {trans_disp} [直填]")
            accepted = self._accept_candidates(direct_fill, collection, result, lock)
            if accepted is None:
                _log("  ❌ 术语精确匹配结果未能持久化，将在后续运行中重试")
                direct_fill = {}
            else:
                direct_success = accepted

        t_terms = time.perf_counter()
        if not llm_entries:
            if _timing_out is not None:
                _timing_out.update({"t_terms": t_terms - t0, "t_llm": 0.0, "t_parse": 0.0})
            return direct_success

        # ── LLM 翻译剩余条目 ──────────────────────────────────────────────────
        _log("  LLM 响应中...")
        t_llm_elapsed = 0.0
        t_parse_elapsed = 0.0

        def _prepare_messages() -> list[dict]:
            nonlocal t_terms
            # The factory runs only after the shared request lease is admitted,
            # so queued work observes terms published by earlier completions.
            with self._in_flight_lock:
                in_flight_snapshot = dict(self._in_flight_terms)
            scoped_term_matches = self._term_mgr.match_terms_scoped(
                entries=llm_entries,
                enable_semantic=getattr(self._cfg.llm_config, "enable_semantic_match", True),
                max_terms=getattr(self._cfg.llm_config, "max_terms_per_batch", 200),
                in_flight_terms=in_flight_snapshot,
            )
            matched_terms = dict(scoped_term_matches.flat_terms)
            terms_by_llm_entry = {
                entry.key: dict(scoped_term_matches.terms_by_entry.get(entry.key, {})) for entry in llm_entries
            }
            for entry in llm_entries:
                required = (required_terms_by_entry or {}).get(entry.key, {})
                if required:
                    resolved_required: dict[str, str] = {}
                    context_resolver = getattr(self._term_mgr, "lookup_context_for_entry", None)
                    lookup_context = context_resolver(entry) if callable(context_resolver) else None
                    for term, fallback_translation in required.items():
                        canonical = (
                            self._term_mgr.resolve_term(term, lookup_context)
                            if lookup_context is not None
                            else self._term_mgr.resolve_term(term)
                        )
                        if canonical is None or not canonical.translation.strip():
                            raise ValueError(f"术语冲突重翻取消：权威术语已不存在：{term}")
                        if canonical.translation != fallback_translation:
                            _log(
                                f"  权威术语已刷新：{term}→{fallback_translation} 改为 "
                                f"{canonical.term}→{canonical.translation}"
                            )
                        resolved_required[canonical.term] = canonical.translation
                    effective_required_terms_by_entry[entry.key] = resolved_required
                    matched_terms.update(resolved_required)
                    terms_by_llm_entry[entry.key].update(resolved_required)
            messages = self._builder.build_translation_prompt(
                llm_entries,
                matched_terms,
                batch.batch_type,
                terms_by_entry=terms_by_llm_entry,
            )
            t_terms = time.perf_counter()

            if stream_callback:
                load_log = self._term_mgr.get_load_log()
                debug_info = (
                    f"\n{'=' * 60}\n"
                    f"[DEBUG] 术语库加载情况\n"
                    f"{'=' * 60}\n"
                    f"ParaTranz client: {self._paratranz_client is not None}\n"
                    f"project_id: {self._project_id}\n"
                )
                for source, count, err in load_log:
                    if err:
                        debug_info += f"  [{source}] 加载失败: {err}\n"
                    else:
                        debug_info += f"  [{source}]: {count} 条\n"
                debug_info += f"合并后术语总数: {len(self._term_mgr._merged_terms)}\n"
                debug_info += f"\n{'=' * 60}\n[DEBUG] 匹配到的术语 ({len(matched_terms)} 个)\n{'=' * 60}\n"
                for term, trans in matched_terms.items():
                    debug_info += f"  {term} → {trans}\n"
                prompt_header = (
                    f"{debug_info}"
                    f"\n{'=' * 60}\n"
                    f"[REQUEST TO LLM]\n"
                    f"{'=' * 60}\n"
                    f"--- SYSTEM ---\n"
                    f"{messages[0]['content']}\n"
                    f"--- USER ---\n"
                    f"{messages[1]['content']}\n"
                    f"{'=' * 60}\n"
                    f"[RESPONSE FROM LLM]\n"
                    f"{'=' * 60}\n"
                )
                stream_callback(prompt_header)
            return messages

        expected_keys = {e.key for e in llm_entries}
        key_to_entry = {e.key: e for e in llm_entries}
        effective_required_terms_by_entry: dict[str, dict[str, str]] = {}
        max_orig_repeat = max((self._max_consecutive_repeat(e.original) for e in llm_entries), default=0)
        _stream_buffer: list[str] = []
        _stream_translations: dict[str, str] = {}  # 流式阶段已完整捕获、尚待最终接纳的翻译
        _stream_invalid_ids: set[str] = set()

        def _chunk_cb(chunk: str):
            _stream_buffer.append(chunk)
            if stream_callback:
                stream_callback(chunk)
            # 增量解析：即时展示完整结果项；最终响应验证或显式 salvage 后才接纳候选。
            partial = {} if repair_mode else self._builder.extract_partial_pairs("".join(_stream_buffer))
            for eid in tuple(_stream_translations):
                if eid not in partial:
                    _stream_invalid_ids.add(eid)
                    _stream_translations.pop(eid, None)
            for eid, trans in partial.items():
                if eid not in _stream_translations and eid not in _stream_invalid_ids and eid in expected_keys:
                    entry = key_to_entry[eid]
                    # 流式阶段实时检测异常重复/回显
                    if self._is_translation_abnormal(entry.original, trans):
                        raise _RepetitionDetected(entry_id=eid)
                    orig_disp = entry.original[:60] + "…" if len(entry.original) > 60 else entry.original
                    trans_disp = trans[:60] + "…" if len(trans) > 60 else trans
                    _log(f"{orig_disp} -> {trans_disp}")
                    _stream_translations[eid] = trans
            # 对整个 buffer 做兜底检测（应对 JSON 尚未闭合但已明显失控的情况）
            is_abnormal, _ = self._detect_stream_repetition(_stream_buffer, max_orig_repeat=max_orig_repeat)
            if is_abnormal:
                raise _RepetitionDetected()

        def _accept_stream_salvage() -> int:
            if not _stream_translations:
                return 0
            accepted = self._accept_candidates(_stream_translations, collection, result, lock)
            if accepted is None:
                _log("  ❌ 流式暂存结果未能持久化，将在后续运行中重试")
                return 0
            for eid, trans in _stream_translations.items():
                entry = key_to_entry[eid]
                if progress_emit:
                    progress_emit()
                ctx = entry.context.split("|")[0] if "|" in (entry.context or "") else entry.context
                if update_terms and ctx in AUTO_TERM_CONTEXTS:
                    with self._in_flight_lock:
                        self._in_flight_terms[entry.original] = trans
            if update_terms and batch.batch_type == "对话":
                salvaged_entries = [key_to_entry[eid] for eid in _stream_translations]
                self._extract_dialogue_terms(salvaged_entries, _stream_translations, result, lock, _log)
            return accepted

        def _recover_remaining_after_stream_failure(reason: str) -> int:
            accepted = _accept_stream_salvage()
            remaining = [e for e in llm_entries if e.key not in _stream_translations]
            if remaining and len(llm_entries) > _min_size:
                from transbridge.ai_translator.batch_planner import Batch as _Batch

                if len(remaining) == len(llm_entries):
                    middle = len(remaining) // 2
                    retry_groups = (remaining[:middle], remaining[middle:])
                else:
                    retry_groups = (remaining,)
                _log(f"  ↩ {len(remaining)} 条{reason}，拆分重试")
                for retry_entries in retry_groups:
                    sub = _Batch(
                        entries=retry_entries,
                        batch_type=batch.batch_type,
                        quest_formid=batch.quest_formid,
                    )
                    self._run_batch(
                        sub,
                        collection,
                        result,
                        lock,
                        log_callback,
                        pause_event,
                        stop_event,
                        stream_callback,
                        _min_size,
                        progress_emit=progress_emit,
                    )
            elif remaining:
                _log(f"  ⚠ {len(remaining)} 条{reason}（已缩至最小）")
                with lock:
                    for entry in remaining:
                        result.failed_entries.append(f"{entry.id}: {reason}")
                    result.failed_count += len(remaining)
            return direct_success + accepted

        t_llm_start = time.perf_counter()
        try:
            response = self._monitored_chat(
                None,
                self._cfg.llm_config.max_output_tokens,
                pause_event,
                stop_event,
                chunk_callback=_chunk_cb,
                messages_factory=_prepare_messages,
            )
        except _RepetitionDetected as exc:
            _log(f"⚠ 检测到重复输出（entry: {exc.entry_id or 'unknown'}），尝试截断修复并拆分重试")
            # 尝试从截断的 buffer 中 salvaging 翻译
            if not repair_mode and exc.entry_id is None and _stream_buffer:
                salvaged = self._salvage_from_repetition_buffer(
                    _stream_buffer, expected_keys, max_orig_repeat=max_orig_repeat
                )
                for eid, trans in salvaged.items():
                    if (
                        eid not in _stream_translations
                        and eid in expected_keys
                        and not self._is_translation_abnormal(key_to_entry[eid].original, trans)
                    ):
                        entry = key_to_entry[eid]
                        _log(f"  [修复] {entry.original[:60]} -> {trans[:60]}")
                        _stream_translations[eid] = trans
            accepted = _recover_remaining_after_stream_failure("因重复输出未获得有效译文")
            if _timing_out is not None:
                _timing_out.update({
                    "t_terms": t_terms - t0,
                    "t_llm": time.perf_counter() - t_llm_start,
                    "t_parse": 0.0,
                })
            return accepted
        except LlmStructuredOutputTruncatedError:
            _log("⚠ Structured Outputs 响应因输出上限截断，保留完整结果项并拆分重试缺项")
            accepted = _recover_remaining_after_stream_failure("因结构化响应截断未获得译文")
            if _timing_out is not None:
                _timing_out.update({
                    "t_terms": t_terms - t0,
                    "t_llm": time.perf_counter() - t_llm_start,
                    "t_parse": 0.0,
                })
            return accepted
        except Exception as exc:
            err_msg = str(exc)
            _log(f"❌ API 调用失败（{len(llm_entries)} 条）: {err_msg}")
            with lock:
                # 非截断异常不会接纳尚未通过完整结构化响应验证的流式暂存项。
                for e in llm_entries:
                    result.failed_entries.append(f"{e.id}: {err_msg}")
                result.failed_count += len(llm_entries)
            if _timing_out is not None:
                _timing_out.update({
                    "t_terms": t_terms - t0,
                    "t_llm": time.perf_counter() - t_llm_start,
                    "t_parse": 0.0,
                })
            return direct_success

        t_llm_elapsed = time.perf_counter() - t_llm_start

        t_parse_start = time.perf_counter()
        # 兜底解析：仅处理流式阶段未捕获的剩余条目
        remaining_ids = expected_keys - _stream_translations.keys()
        fallback_translations = self._builder.parse_translation_response(response, remaining_ids)
        t_parse_elapsed = time.perf_counter() - t_parse_start

        constraint_failures: set[str] = set()
        if repair_mode:
            for eid, translation in tuple(fallback_translations.items()):
                required = effective_required_terms_by_entry.get(eid, {})
                missing_terms = [
                    f"{term}→{target}"
                    for term, target in required.items()
                    if not self._contains_required_translation(translation, target)
                ]
                abnormal = eid in key_to_entry and self._is_translation_abnormal(
                    key_to_entry[eid].original,
                    translation,
                )
                if missing_terms or abnormal:
                    constraint_failures.add(eid)
                    fallback_translations.pop(eid, None)
                    if missing_terms:
                        _log(f"⚠ 术语冲突重翻未采用权威译法：{', '.join(missing_terms)}")
                    if abnormal:
                        _log("⚠ 术语冲突重翻返回异常重复文本，已拒绝写回")

        # 记录兜底阶段获取的条目日志（流式阶段的已在 _chunk_cb 中实时记录）
        for eid, trans in fallback_translations.items():
            orig = key_to_entry[eid].original if eid in key_to_entry else eid
            orig_disp = orig[:60] + "…" if len(orig) > 60 else orig
            trans_disp = trans[:60] + "…" if len(trans) > 60 else trans
            _log(f"{orig_disp} -> {trans_disp}")

        # 合并全量翻译结果（供后续 missing/动态术语/对话抽取逻辑使用）
        id_to_translation = {**_stream_translations, **fallback_translations}
        missing = expected_keys - id_to_translation.keys()

        # 有未获得译文且批次可继续拆分 → 对 missing 条目重试
        if missing and len(llm_entries) > _min_size:
            missing_entries = [e for e in llm_entries if e.key in missing]
            if len(missing_entries) == len(llm_entries):
                mid = len(llm_entries) // 2
                halves = [llm_entries[:mid], llm_entries[mid:]]
                _log(f"  ↩ {len(missing)}/{len(llm_entries)} 条未获译文，对半拆分重试")
            else:
                halves = [missing_entries]
                _log(f"  ↩ {len(missing)} 条未获译文，单独重试")
            from transbridge.ai_translator.batch_planner import Batch as _Batch

            accepted = self._accept_candidates(id_to_translation, collection, result, lock)
            success = 0 if accepted is None else accepted
            for _ in _stream_translations:
                if progress_emit:
                    progress_emit()
            if accepted is not None and id_to_translation:
                auto_term_entries = [
                    e
                    for e in llm_entries
                    if (e.context.split("|")[0] if "|" in (e.context or "") else e.context) in AUTO_TERM_CONTEXTS
                ]
                if auto_term_entries:
                    self._update_dynamic_terms(auto_term_entries, id_to_translation, result, lock)
                if batch.batch_type == "对话":
                    self._extract_dialogue_terms(llm_entries, id_to_translation, result, lock, _log)
            for half in halves:
                sub = _Batch(entries=half, batch_type=batch.batch_type, quest_formid=batch.quest_formid)
                self._run_batch(
                    sub,
                    collection,
                    result,
                    lock,
                    log_callback,
                    pause_event,
                    stop_event,
                    stream_callback,
                    _min_size,
                    progress_emit=progress_emit,
                )
            if _timing_out is not None:
                _timing_out.update({"t_terms": t_terms - t0, "t_llm": t_llm_elapsed, "t_parse": t_parse_elapsed})
            return success + direct_success

        if missing:
            _log(f"  ⚠ {len(missing)} 条未获得译文（已缩至单条，无法继续拆分）")
            with lock:
                for eid in sorted(missing):
                    entry = key_to_entry[eid]
                    if repair_mode:
                        reason = "未采用权威术语" if eid in constraint_failures else "模型未返回有效译文"
                        result.failed_entries.append(f"{entry.id}: 术语冲突重翻失败：{reason}")
                    else:
                        result.failed_entries.append(f"{entry.id}: 模型未返回有效译文")
                result.failed_count += len(missing)

        accepted = self._accept_candidates(id_to_translation, collection, result, lock)
        success = 0 if accepted is None else accepted
        for _ in _stream_translations:
            if progress_emit:
                progress_emit()

        # 自动写入动态术语库（仅 context 属于 AUTO_TERM_CONTEXTS 的条目，直接填充的来源已在术语库中）
        auto_term_entries = [
            e
            for e in llm_entries
            if (e.context.split("|")[0] if "|" in (e.context or "") else e.context) in AUTO_TERM_CONTEXTS
        ]
        if accepted is not None and update_terms and auto_term_entries:
            self._update_dynamic_terms(auto_term_entries, id_to_translation, result, lock)

        # 从对话批次抽取专有名词
        if accepted is not None and update_terms and batch.batch_type == "对话" and id_to_translation:
            self._extract_dialogue_terms(llm_entries, id_to_translation, result, lock, _log)

        if _timing_out is not None:
            _timing_out.update({"t_terms": t_terms - t0, "t_llm": t_llm_elapsed, "t_parse": t_parse_elapsed})
        return success + direct_success

    def _accept_candidates(
        self,
        id_to_translation: dict[str, str],
        collection: TranslationEntryCollection,
        result: TranslationResult,
        lock: threading.Lock,
    ) -> int | None:
        if self._candidate_session is None:
            raise RuntimeError("translation candidate session was not initialized")
        try:
            with lock:
                accepted = self._candidate_session.accept(id_to_translation, collection)
                result.success_count += accepted.accepted
        except Exception as exc:
            with lock:
                for entry_id in id_to_translation:
                    result.failed_entries.append(f"{entry_id}: 候选结果持久化失败：{exc}")
                result.failed_count += len(id_to_translation)
            return None
        return accepted.accepted

    def _update_dynamic_terms(
        self,
        entries: list,
        id_to_translation: dict[str, str],
        result: TranslationResult,
        lock: threading.Lock,
    ) -> None:
        terms = []
        for entry in entries:
            if entry.id in id_to_translation:
                translation = id_to_translation[entry.id]
                original = entry.original
                if original and translation:
                    terms.append((original, translation, "auto_name", entry.context or ""))
        if terms:
            self._term_mgr.get_dynamic_db().add_many_and_save(terms)
            with lock:
                result.new_dynamic_terms += len(terms)

    def _extract_dialogue_terms(
        self,
        entries: list,
        id_to_translation: dict[str, str],
        result: TranslationResult,
        lock: threading.Lock,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        translated_entries = [entry for entry in entries if entry.id in id_to_translation]
        if not translated_entries:
            return
        from transbridge.application.translation.token_batching import StableContentBatcher
        from transbridge.infra.token_counting import TiktokenContentTokenCounter

        plan = StableContentBatcher(
            TiktokenContentTokenCounter(self._cfg.llm_config.model),
            self._cfg.llm_config.max_tokens_per_batch,
        ).plan(
            translated_entries,
            key=lambda entry: entry.identity,
            content=lambda entry: (entry.original, id_to_translation[entry.id]),
        )
        for oversized in plan.oversized:
            if log_callback is not None:
                log_callback(f"⚠ 对话术语抽取已跳过：{oversized.message}")
        extracted = []
        for batch in plan.batches:
            extracted.extend(
                self._extractor.extract([
                    {"original": entry.original, "translation": id_to_translation[entry.id]} for entry in batch.items
                ])
            )
        if extracted:
            # 过滤掉所有来源（dynamic/paratranz/json/excel）中已存在的术语，保留已有译名
            new_terms = [te for te in extracted if not self._term_mgr.has_term(te.term)]
            if new_terms:
                terms = [(te.term, te.translation, te.source, "") for te in new_terms]
                self._term_mgr.get_dynamic_db().add_many_and_save(terms)
                with lock:
                    result.new_dynamic_terms += len(new_terms)


def _terminology_plugin_id(term_manager: object | None, entry: object) -> str | None:
    if term_manager is None:
        return None
    uses_project_context = getattr(term_manager, "_uses_project_context", None)
    if callable(uses_project_context) and not uses_project_context():
        return None
    lookup = getattr(term_manager, "lookup_context_for_entry", None)
    if not callable(lookup):
        return None
    context = lookup(entry)
    plugin_id = None if context is None else getattr(context, "plugin_id", None)
    return plugin_id if isinstance(plugin_id, str) and plugin_id.strip() else None
