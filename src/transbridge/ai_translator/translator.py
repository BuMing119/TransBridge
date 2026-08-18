"""
自动翻译主控器。

AutoTranslator.translate() 驱动整个翻译流程：
  1. 批次规划
  2. 按轮次依序执行批次翻译（轮次间刷新术语库）
  3. 将结果写回集合（stage=2，表示AI翻译待审核）
  4. 专有名词批次完成后自动写入动态术语库
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import TYPE_CHECKING, Callable

from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY


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
        if DEFAULT_STAGE_POLICY.allows_ai(
            entry.stage, entry.translation, original=entry.original
        )
    ]


def _select_post_process_candidates(entries: list, target_entry_ids: list[str] | None) -> list:
    """Keep post-processing inside the same automatic-edit policy as translation."""
    target_set = set(target_entry_ids) if target_entry_ids else None
    return [
        entry
        for entry in entries
        if entry.translation
        and (target_set is None or entry.key in target_set)
        and DEFAULT_STAGE_POLICY.allows_ai(
            entry.stage, entry.translation, original=entry.original
        )
    ]


if TYPE_CHECKING:
    from transbridge.ai_translator.batch_planner import Batch
    from transbridge.ai_translator.post_processor import PostProcessResult
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection
    from transbridge.paratranz.config_manager import LLMConfig


@dataclass
class TranslatorConfig:
    llm_config: "LLMConfig"
    esp_path: str
    overwrite: bool = False    # True = 全部重翻，False = 仅翻未翻译


@dataclass
class TranslationResult:
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    new_dynamic_terms: int = 0
    failed_entries: list[str] = field(default_factory=list)
    post_process_result: "PostProcessResult | None" = None  # 后处理结果
    refine_results: dict | None = None     # {entry_id: RefineResult}，后处理修复中间数据
    polish_results: dict | None = None     # {entry_id: PolishResult}，后处理润色中间数据
    decisions: dict | None = None          # {entry_id: ArbiterDecision}，后处理裁决中间数据
    report_path: str | None = None         # 生成的 Excel 报告路径


@dataclass
class ProgressCheckpoint:
    """批次级断点，持久化到 data/ai_translator/{esp_stem}/{esp_stem}_progress.json。"""
    esp_stem: str
    target_entry_ids: list[str] | None
    overwrite: bool
    completed_fingerprints: list[list[str]]   # 每项为已完成批次的排序 entry id 列表
    result_so_far: dict                        # success_count / failed_count / new_dynamic_terms
    run_id: str = ""

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
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, esp_path: str) -> "ProgressCheckpoint | None":
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
    ):
        self._cfg = config
        self._paratranz_client = paratranz_client
        self._project_id = project_id
        self._candidate_checkpoint_port = candidate_checkpoint
        self._run_id_factory = run_id_factory or (
            lambda: f"translation-{secrets.token_hex(16)}"
        )
        self._candidate_session = None

        from transbridge.infra.llm_client import create_llm_client
        from transbridge.ai_translator.prompt_builder import PromptBuilder
        from transbridge.ai_translator.term_database import TermDatabaseManager
        from transbridge.ai_translator.noun_extractor import NounExtractor
        from transbridge.ai_translator.batch_planner import BatchPlanner

        self._llm = create_llm_client(config.llm_config)
        self._builder = PromptBuilder(
            game_profile=config.llm_config.game_profile,
            target_lang=config.llm_config.target_lang,
        )
        self._term_mgr = TermDatabaseManager(
            config=config.llm_config,
            esp_path=config.esp_path,
            paratranz_client=paratranz_client,
            project_id=project_id,
        )
        self._extractor = NounExtractor(self._llm, self._builder)
        self._planner = BatchPlanner(max_tokens_per_batch=config.llm_config.max_tokens_per_batch)

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
        messages: list[dict],
        max_tokens: int,
        pause_event: threading.Event | None,
        stop_event: threading.Event | None,
        chunk_callback: Callable[[str], None] | None = None,
    ) -> str:
        """运行 LLM 调用，期间每 50ms 检查 pause_event / stop_event；触发时立即取消请求。
        chunk_callback: 非 None 时启用流式调用，每收到一个文本块即回调。
        """
        result_holder: list = [None]
        error_holder: list = [None]
        done = threading.Event()

        def _call():
            try:
                if chunk_callback is not None:
                    result_holder[0] = self._llm.chat_stream(messages, max_tokens, chunk_callback)
                else:
                    result_holder[0] = self._llm.chat(messages, max_tokens)
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
            (len(m.group(0)) for m in re.finditer(r'(.|.{2}|.{3})\1*', text)),
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
            for m in re.finditer(r'(.|.{2}|.{3})\1*', text):
                if len(m.group(0)) == buffer_repeat:
                    return True, m.start()
        # 额外兜底：4-10 字符短语的极端重复
        for length, min_count in [(4, 20), (5, 15), (6, 12), (7, 10), (8, 10), (9, 8), (10, 8)]:
            m = re.search(r'(.{' + str(length) + r'})\1{' + str(min_count - 1) + r',}', text)
            if m:
                repeat_total = len(m.group(0))
                if repeat_total > 200 and repeat_total > max_orig_repeat * 1.5 + 20:
                    return True, m.start()
        return False, -1

    def _salvage_from_repetition_buffer(self, buffer: str, expected_keys: set[str], max_orig_repeat: int = 0) -> dict[str, str]:
        """当流式输出出现重复时，截断重复部分并尝试修复 JSON， salvaging 已完成的翻译对。"""
        from transbridge.ai_translator.prompt_builder import _extract_partial_json_pairs
        text = "".join(buffer) if isinstance(buffer, list) else buffer
        is_abnormal, truncate_pos = self._detect_stream_repetition(text, max_orig_repeat=max_orig_repeat)
        if not is_abnormal:
            return {}
        truncated = text[:truncate_pos]
        last_pair_match = None
        for m in re.finditer(r'"((?:[^"\\]|\\.)*?)"\s*:\s*"((?:[^"\\]|\\.)*?)"', truncated):
            last_pair_match = m
        if last_pair_match:
            repaired = truncated[:last_pair_match.end()] + "}"
        else:
            brace_idx = truncated.find("{")
            repaired = truncated[:brace_idx + 1] + "}" if brace_idx != -1 else "{}"
        try:
            data = json.loads(repaired)
            if isinstance(data, dict):
                return {k: str(v) for k, v in data.items() if k in expected_keys and v}
        except Exception:
            pass
        return _extract_partial_json_pairs(repaired)

    @staticmethod
    def _split_last_batch_to_fill_workers(
        batches: list["Batch"],
        max_workers: int
    ) -> list["Batch"]:
        """拆分最后一批以填满并发。

        当批次数 < max_workers 时，将最后一批拆分成多份。
        返回拆分后的批次列表。
        """
        from transbridge.ai_translator.batch_planner import Batch

        if len(batches) >= max_workers or len(batches) == 0:
            return batches

        # 计算需要拆分成多少份
        needed = max_workers - (len(batches) - 1)  # 填满所需份数
        last_batch = batches[-1]

        if len(last_batch.entries) < needed:
            # 条目数不足以拆分，直接返回
            return batches

        # 拆分最后一批
        entries = last_batch.entries
        chunk_size = max(1, len(entries) // needed)
        split_batches = []

        for i in range(needed):
            start = i * chunk_size
            if i == needed - 1:
                chunk = entries[start:]  # 最后一份包含剩余所有
            else:
                chunk = entries[start:start + chunk_size]
            if chunk:
                split_batches.append(Batch(
                    entries=chunk,
                    batch_type=last_batch.batch_type,
                    quest_formid=last_batch.quest_formid,
                ))

        return batches[:-1] + split_batches

    def translate(
        self,
        collection: "TranslationEntryCollection",
        target_entry_ids: list[str] | None,
        progress_callback: Callable[[int, int, str, int, int, int], None],
        stop_event: threading.Event,
        pause_event: threading.Event | None = None,
        checkpoint: "ProgressCheckpoint | None" = None,
        log_callback: Callable[[int, str], None] | None = None,
        stream_callback: Callable[[int, str], None] | None = None,
    ) -> TranslationResult:
        """
        progress_callback(current, total, message, success_count, failed_count, new_terms)
        log_callback(batch_idx, line)  — batch_idx=-1 为轮次级消息，>=0 为批次专属消息
        stream_callback(chunk) — LLM 流式响应片段回调，可为 None
        pause_event: set=运行中，clear=暂停中（wait() 会阻塞直到 set）
        checkpoint: 传入则从断点继续，跳过已完成批次
        """
        result = TranslationResult()
        lock = threading.Lock()
        t_total_start = time.perf_counter()

        # 清空 in-flight 缓存（新翻译会话）
        # 仅当缓存是本实例拥有时才清空，批量翻译时使用共享缓存不应清空
        if self._owns_in_flight_cache:
            with self._in_flight_lock:
                self._in_flight_terms.clear()

        # 从断点恢复累计统计
        completed_fps: set[frozenset] = set()
        if checkpoint and checkpoint.run_id:
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
            )
            for entry in candidates
        )
        spec_fingerprint = canonical_hash(
            {
                "run_id": run_id,
                "config_revision": self._cfg.llm_config.config_revision,
                "provider": self._cfg.llm_config.provider,
                "base_url": self._cfg.llm_config.base_url,
                "model": self._cfg.llm_config.model,
                "target_lang": self._cfg.llm_config.target_lang,
                "scope": [entry.entry_key.to_dict() for entry in translation_inputs],
            }
        )
        candidate_port = self._candidate_checkpoint_port
        if candidate_port is None:
            checkpoint_root = Path(
                self._cfg.llm_config.get_ai_translator_dir(Path(self._cfg.esp_path).stem)
            ) / "runtime-v2"
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

        max_workers = self._cfg.llm_config.max_concurrent

        # 规划批次（传入 max_workers 以启用自适应批次大小）
        plan = self._planner.plan(candidates, max_workers=max_workers)
        all_batches = plan.all_batches()
        total_batches = len(all_batches)
        total_entries = sum(len(b.entries) for b in all_batches)

        # 断点续传：将已完成的词条数加到总计中，避免 current 超过 total
        if checkpoint and checkpoint.run_id:
            completed_from_checkpoint = checkpoint.result_so_far.get(
                "success_count", 0
            ) + checkpoint.result_so_far.get("failed_count", 0)
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
                    done_entries, total_entries, full_msg,
                    result.success_count, result.failed_count, result.new_dynamic_terms,
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

        def _save_checkpoint():
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
                )
            cp.save(self._cfg.esp_path)

        def _run_one_batch(batch: "Batch", round_name: str) -> None:
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
            _blog(f"\n开始翻译：")
            _blog(f"任务{idx}：{batch.batch_type}")
            _blog(f"-----------------------")

            t_batch = time.perf_counter()
            _batch_timing: dict = {}
            with lock:
                _success_before = result.success_count
                _terms_before = result.new_dynamic_terms

            _progress_emit = lambda: _emit(msg)
            try:
                self._run_batch(batch, collection, result, lock, _batch_log_cb, pause_event, stop_event, _per_batch_stream, _timing_out=_batch_timing, progress_emit=_progress_emit)
            except _CancelledByStop:
                _blog("⏹ 批次已中断（停止）")
                _save_checkpoint()
                return
            except _CancelledByPause:
                _blog("⏸ 批次已中断（暂停）")
                if pause_event is not None:
                    pause_event.wait()   # 阻塞直到用户点击继续
                if stop_event.is_set():
                    _save_checkpoint()
                    return
                # 继续后重试本批次（不重新计数，沿用原 idx）
                _emit(f"{round_name} | {batch.batch_type}（{len(batch.entries)} 条，重试）")
                _batch_timing.clear()
                with lock:
                    _success_before = result.success_count
                    _terms_before = result.new_dynamic_terms
                self._run_batch(batch, collection, result, lock, _batch_log_cb, pause_event, stop_event, _per_batch_stream, _timing_out=_batch_timing, progress_emit=_progress_emit)

            t_batch_elapsed = time.perf_counter() - t_batch
            with lock:
                _success_delta = result.success_count - _success_before
                _terms_delta = result.new_dynamic_terms - _terms_before

            # 批次尾
            _blog(f"-----------------------")
            _blog(f"已完成：")
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

        # ── 第一轮：所有批次并发 ──────────────────────────────────────────────
        if plan.round1 and not stop_event.is_set():
            t_round = time.perf_counter()
            _log(f"\n── 第一轮开始（{len(plan.round1)} 批，专有名词）──")

            # 动态拆分最后一批填满并发
            batches_to_run = self._split_last_batch_to_fill_workers(plan.round1, max_workers)
            if len(batches_to_run) > len(plan.round1):
                _log(f"  最后一批拆分为 {len(batches_to_run) - len(plan.round1) + 1} 份以填满并发")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(_run_one_batch, batch, "第一轮")
                    for batch in batches_to_run
                ]
                for f in as_completed(futures):
                    f.result()   # 传播异常
            _log(f"── 第一轮完成: {time.perf_counter() - t_round:.2f}s ──\n")

        # ── 第二轮：quest 间并发，quest 内串行 ───────────────────────────────
        if plan.round2 and not stop_event.is_set():
            t_round = time.perf_counter()
            _log(f"\n── 第二轮开始（{len(plan.round2)} 批，对话）──")

            def _run_quest_group(quest_batches: list["Batch"]) -> None:
                for batch in quest_batches:
                    if stop_event.is_set():
                        break
                    _run_one_batch(batch, "第二轮")

            # 过滤已完成批次，基于剩余批次决定并发策略
            pending_round2 = [
                b for b in plan.round2
                if frozenset(e.key for e in b.entries) not in completed_fps
            ]

            if not pending_round2:
                _log(f"── 第二轮无待处理批次 ──\n")
            else:
                # 按 quest 分组
                pending_quest_groups: dict[str, list["Batch"]] = {}
                for batch in pending_round2:
                    pending_quest_groups.setdefault(batch.quest_formid or "", []).append(batch)

                # 决定并发策略
                if len(pending_quest_groups) <= max_workers:
                    # quest 数不足以填满线程池 → 批次级并发（拆分最后一批）
                    batches_to_run = self._split_last_batch_to_fill_workers(pending_round2, max_workers)
                    strategy = "批次级（含拆分）" if len(batches_to_run) > len(pending_round2) else "批次级"
                    _log(f"  活跃 quest 数={len(pending_quest_groups)}, max_workers={max_workers}, 未完成任务数={len(pending_round2)}, 并发策略={strategy}")

                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = [
                            executor.submit(_run_one_batch, batch, "第二轮")
                            for batch in batches_to_run
                        ]
                        for f in as_completed(futures):
                            f.result()
                else:
                    # quest 组级并发（quest 内串行，不拆分）
                    quest_groups = plan.round2_by_quest()
                    _log(f"  活跃 quest 数={len(pending_quest_groups)}, max_workers={max_workers}, 未完成任务数={len(pending_round2)}, 并发策略=quest组级")

                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = [
                            executor.submit(_run_quest_group, batches)
                            for batches in quest_groups.values()
                        ]
                        for f in as_completed(futures):
                            f.result()
                _log(f"── 第二轮完成: {time.perf_counter() - t_round:.2f}s ──\n")

        # ── 第三轮：所有批次并发 ──────────────────────────────────────────────
        if plan.round3 and not stop_event.is_set():
            t_round = time.perf_counter()
            _log(f"\n── 第三轮开始（{len(plan.round3)} 批，长文本）──")

            # 动态拆分最后一批填满并发
            batches_to_run = self._split_last_batch_to_fill_workers(plan.round3, max_workers)
            if len(batches_to_run) > len(plan.round3):
                _log(f"  最后一批拆分为 {len(batches_to_run) - len(plan.round3) + 1} 份以填满并发")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(_run_one_batch, batch, "第三轮")
                    for batch in batches_to_run
                ]
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
                    permissions=frozenset(
                        {"entry.translation.write", "entry.stage.write"}
                    ),
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
                f"{diagnostic.code}: {diagnostic.message}"
                for diagnostic in commit.diagnostics
            )

        # ── 后处理：质量检查 ─────────────────────────────────────────────────────
        if not stop_event.is_set() and result.success_count > 0 and getattr(self._cfg.llm_config, 'enable_post_process', True):
            _log(f"\n── 开始质量检查 ──")
            from transbridge.application.contracts import OperationOutcome, RequestContext
            from transbridge.application.io import StagePolicy
            from transbridge.application.io.publish import ImmediateCommitGuard
            from transbridge.application.translation import (
                CheckerStage,
                FilesystemPostProcessCheckpointPort,
                FilesystemTranslationCheckpointPort,
                LlmPostProcessStage,
                OpenAiPostProcessHttpPort,
                PostProcessExecutionService,
                PostProcessLlmPhase,
                PostProcessWorkload,
                TranslationInput,
            )

            from .post_processor import PostProcessor, PostProcessorConfig

            # 从LLMConfig加载后处理配置
            pp_config = PostProcessorConfig.from_llm_config(self._cfg.llm_config)
            # 根据LLM可用性调整质量关卡
            if not bool(self._llm):
                pp_config.enable_quality_gate = False
            post_processor = PostProcessor(pp_config)
            post_processor.register_default_checkers(
                term_manager=self._term_mgr,
                llm_client=self._llm,
            )

            # 只对成功翻译的条目进行后处理
            # 收集本次翻译成功的条目
            all_entries = list(collection)
            entries_to_check = _select_post_process_candidates(
                all_entries, target_entry_ids
            )

            if entries_to_check:
                stages = []
                stage_names = []
                checker_phases = {
                    "ConsistencyChecker": "consistency",
                    "FormatValidator": "format",
                    "QualityGateChecker": "quality_gate",
                }
                for checker in post_processor._checkers:
                    phase = checker_phases.get(type(checker).__name__)
                    if phase is not None:
                        stages.append(CheckerStage(phase, checker))
                        stage_names.append(phase)
                llm_port = OpenAiPostProcessHttpPort(
                    credential=lambda: self._cfg.llm_config.api_key
                )
                llm_phases = (
                    (pp_config.enable_refinement, PostProcessLlmPhase.REFINE, "refinement"),
                    (pp_config.enable_polish, PostProcessLlmPhase.POLISH, "polish"),
                    (pp_config.enable_llm_arbitration, PostProcessLlmPhase.ARBITRATE, "arbitration"),
                )
                for enabled, phase, phase_name in llm_phases:
                    if enabled:
                        stages.append(
                            LlmPostProcessStage(
                                phase,
                                llm_port,
                                target_locale=self._cfg.llm_config.target_lang,
                                game_profile=self._cfg.llm_config.game_profile,
                                base_url=self._cfg.llm_config.base_url,
                                model=self._cfg.llm_config.model,
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
                post_run_id = f"{run_id}:postprocess"
                inputs = tuple(
                    TranslationInput(
                        entry.identity,
                        entry.revision,
                        entry.original,
                        entry.translation,
                        entry.stage,
                        entry.context or "",
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
                        permissions=frozenset(
                            {"entry.translation.write", "entry.stage.write"}
                        ),
                    ),
                    commit_guard=ImmediateCommitGuard(
                        post_run_id, active=lambda: not stop_event.is_set()
                    ),
                    commit_checkpoint=FilesystemTranslationCheckpointPort(
                        checkpoint_root / "postprocess-commit"
                    ),
                    is_cancelled=stop_event.is_set,
                    run_spec_summary={
                        "source": "auto-translator",
                        "model": self._cfg.llm_config.model,
                    },
                )
                pp_result = execution.report_result.value
                if pp_result is None:
                    codes = ", ".join(item.code for item in execution.report_result.diagnostics)
                    result.failed_entries.append(f"后处理失败: {codes or 'POSTPROCESS_FAILED'}")
                else:
                    _log(f"质量检查完成：检查 {pp_result.input_count} 条")
                    _log(f"  发现问题：{pp_result.issue_count} 个")
                    result.post_process_result = pp_result
                    if execution.commit_result is not None and execution.commit_result.outcome not in {
                        OperationOutcome.COMPLETED,
                        OperationOutcome.PARTIAL,
                    }:
                        result.failed_entries.extend(
                            f"{diagnostic.code}: {diagnostic.message}"
                            for diagnostic in execution.commit_result.diagnostics
                        )
            else:
                _log(f"无可检查的条目")

        elif not stop_event.is_set() and result.success_count > 0:
            _log(f"\n── 质量检查已跳过（用户设置）──")

        if not stop_event.is_set():
            # 翻译与后处理均完成，删除翻译断点
            ProgressCheckpoint(esp_stem, target_entry_ids, self._cfg.overwrite, [], {}).delete(self._cfg.esp_path)
        elif stop_event.is_set():
            # 被中断时保存翻译断点（若后处理被中断，翻译断点也保留）
            _save_checkpoint()

        _log(f"\n总耗时: {t_total:.2f}s")
        return result

    def _run_batch(
        self,
        batch: "Batch",
        collection: "TranslationEntryCollection",
        result: TranslationResult,
        lock: threading.Lock,
        log_callback: Callable[[str], None] | None = None,
        pause_event: threading.Event | None = None,
        stop_event: threading.Event | None = None,
        stream_callback: Callable[[str], None] | None = None,
        _min_size: int = 1,
        _timing_out: dict | None = None,
        progress_emit: Callable[[], None] | None = None,
    ) -> int:
        from transbridge.converter.context_categories import AUTO_TERM_CONTEXTS

        def _log(line: str):
            if log_callback:
                log_callback(line)

        entries = batch.entries
        if not entries:
            return 0

        t0 = time.perf_counter()
        # 使用增强版两阶段术语匹配（合并 in-flight 缓存）
        with self._in_flight_lock:
            in_flight_snapshot = dict(self._in_flight_terms)
        matched_terms = self._term_mgr.match_terms_enhanced(
            entries=entries,
            enable_semantic=getattr(self._cfg.llm_config, 'enable_semantic_match', True),
            max_terms=getattr(self._cfg.llm_config, 'max_terms_per_batch', 200),
            in_flight_terms=in_flight_snapshot,
        )
        t_terms = time.perf_counter()

        # ── 精确匹配：原文与术语完全相同的条目直接填充，无需发送给 LLM ──────
        exact_orig_to_trans = self._term_mgr.exact_match([e.original for e in entries])
        direct_fill: dict[str, str] = {}   # entry_id → translation
        llm_entries = []
        key_map_all = {e.key: e for e in entries}
        for e in entries:
            if e.original in exact_orig_to_trans:
                direct_fill[e.key] = exact_orig_to_trans[e.original]
            else:
                llm_entries.append(e)

        if direct_fill:
            _log(f"  术语精确匹配: {len(direct_fill)} 条直接填充")
            for eid, trans in direct_fill.items():
                orig = key_map_all[eid].original
                orig_disp = orig[:60] + "…" if len(orig) > 60 else orig
                trans_disp = trans[:60] + "…" if len(trans) > 60 else trans
                _log(f"{orig_disp} -> {trans_disp} [直填]")
            self._accept_candidates(direct_fill, collection, result, lock)

        if not llm_entries:
            if _timing_out is not None:
                _timing_out.update({'t_terms': t_terms - t0, 't_llm': 0.0, 't_parse': 0.0})
            return len(direct_fill)

        # ── LLM 翻译剩余条目 ──────────────────────────────────────────────────
        messages = self._builder.build_translation_prompt(llm_entries, matched_terms, batch.batch_type)
        _log(f"  LLM 响应中...")
        t_llm_elapsed = 0.0
        t_parse_elapsed = 0.0

        # 写入提示词到流式日志
        if stream_callback:
            # 先写入术语加载情况
            load_log = self._term_mgr.get_load_log()
            debug_info = (
                f"\n{'='*60}\n"
                f"[DEBUG] 术语库加载情况\n"
                f"{'='*60}\n"
                f"ParaTranz client: {self._paratranz_client is not None}\n"
                f"project_id: {self._project_id}\n"
            )
            for source, count, err in load_log:
                if err:
                    debug_info += f"  [{source}] 加载失败: {err}\n"
                else:
                    debug_info += f"  [{source}]: {count} 条\n"
            debug_info += f"合并后术语总数: {len(self._term_mgr._merged_terms)}\n"
            debug_info += (
                f"\n{'='*60}\n"
                f"[DEBUG] 匹配到的术语 ({len(matched_terms)} 个)\n"
                f"{'='*60}\n"
            )
            for term, trans in matched_terms.items():
                debug_info += f"  {term} → {trans}\n"

            # 再写入提示词
            prompt_header = (
                f"{debug_info}"
                f"\n{'='*60}\n"
                f"[REQUEST TO LLM]\n"
                f"{'='*60}\n"
                f"--- SYSTEM ---\n"
                f"{messages[0]['content']}\n"
                f"--- USER ---\n"
                f"{messages[1]['content']}\n"
                f"{'='*60}\n"
                f"[RESPONSE FROM LLM]\n"
                f"{'='*60}\n"
            )
            stream_callback(prompt_header)

        expected_keys = {e.key for e in llm_entries}
        key_to_entry = {e.key: e for e in llm_entries}
        max_orig_repeat = max((self._max_consecutive_repeat(e.original) for e in llm_entries), default=0)
        _stream_buffer: list[str] = []
        _stream_translations: dict[str, str] = {}  # 流式阶段已捕获并写回的翻译

        def _chunk_cb(chunk: str):
            _stream_buffer.append(chunk)
            if stream_callback:
                stream_callback(chunk)
            # 增量解析：每收到一个 chunk 就尝试从 buffer 中提取新完成的翻译对并立即写回
            partial = self._builder.extract_partial_pairs("".join(_stream_buffer))
            for eid, trans in partial.items():
                if eid not in _stream_translations and eid in expected_keys:
                    entry = key_to_entry[eid]
                    # 流式阶段实时检测异常重复/回显
                    if self._is_translation_abnormal(entry.original, trans):
                        raise _RepetitionDetected(entry_id=eid)
                    orig_disp = entry.original[:60] + "…" if len(entry.original) > 60 else entry.original
                    trans_disp = trans[:60] + "…" if len(trans) > 60 else trans
                    _log(f"{orig_disp} -> {trans_disp}")
                    self._accept_candidates({eid: trans}, collection, result, lock)
                    _stream_translations[eid] = trans
                    if progress_emit:
                        progress_emit()
                    # Round 1 术语即时写入 in-flight 缓存（供并发批次使用）
                    ctx = entry.context.split("|")[0] if "|" in (entry.context or "") else entry.context
                    if ctx in AUTO_TERM_CONTEXTS:
                        with self._in_flight_lock:
                            self._in_flight_terms[entry.original] = trans
            # 对整个 buffer 做兜底检测（应对 JSON 尚未闭合但已明显失控的情况）
            is_abnormal, _ = self._detect_stream_repetition(_stream_buffer, max_orig_repeat=max_orig_repeat)
            if is_abnormal:
                raise _RepetitionDetected()

        t_llm_start = time.perf_counter()
        try:
            response = self._monitored_chat(
                messages, self._cfg.llm_config.max_output_tokens, pause_event, stop_event,
                chunk_callback=_chunk_cb,
            )
        except _RepetitionDetected as exc:
            _log(f"⚠ 检测到重复输出（entry: {exc.entry_id or 'unknown'}），尝试截断修复并拆分重试")
            # 尝试从截断的 buffer 中 salvaging 翻译
            if exc.entry_id is None and _stream_buffer:
                salvaged = self._salvage_from_repetition_buffer(_stream_buffer, expected_keys, max_orig_repeat=max_orig_repeat)
                for eid, trans in salvaged.items():
                    if eid not in _stream_translations and eid in expected_keys:
                        entry = key_to_entry[eid]
                        _log(f"  [修复] {entry.original[:60]} -> {trans[:60]}")
                        self._accept_candidates({eid: trans}, collection, result, lock)
                        _stream_translations[eid] = trans
                        if progress_emit:
                            progress_emit()
                        ctx = entry.context.split("|")[0] if "|" in (entry.context or "") else entry.context
                        if ctx in AUTO_TERM_CONTEXTS:
                            with self._in_flight_lock:
                                self._in_flight_terms[entry.original] = trans
            remaining = [e for e in llm_entries if e.key not in _stream_translations]
            if remaining:
                if len(remaining) > _min_size:
                    mid = len(remaining) // 2
                    halves = [remaining[:mid], remaining[mid:]]
                    _log(f"  ↩ {len(remaining)} 条拆分重试")
                    from transbridge.ai_translator.batch_planner import Batch as _Batch
                    for half in halves:
                        sub = _Batch(entries=half, batch_type=batch.batch_type, quest_formid=batch.quest_formid)
                        self._run_batch(sub, collection, result, lock, log_callback, pause_event, stop_event, stream_callback, _min_size, progress_emit=progress_emit)
                else:
                    _log(f"  ⚠ {len(remaining)} 条因重复输出无法翻译（已缩至最小）")
                    with lock:
                        for e in remaining:
                            result.failed_entries.append(f"{e.id}: 模型重复输出")
                        result.failed_count += len(remaining)
            if _timing_out is not None:
                _timing_out.update({'t_terms': t_terms - t0, 't_llm': time.perf_counter() - t_llm_start, 't_parse': 0.0})
            return len(direct_fill) + len(_stream_translations)
        except Exception as exc:
            err_msg = str(exc)
            _log(f"❌ API 调用失败（{len(llm_entries)} 条）: {err_msg}")
            with lock:
                # 流式阶段已成功写回的条目不计入失败
                truly_failed = [e for e in llm_entries if e.key not in _stream_translations]
                for e in truly_failed:
                    result.failed_entries.append(f"{e.id}: {err_msg}")
                result.failed_count += len(truly_failed)
            if _timing_out is not None:
                _timing_out.update({'t_terms': t_terms - t0, 't_llm': time.perf_counter() - t_llm_start, 't_parse': 0.0})
            return len(direct_fill)

        t_llm_elapsed = time.perf_counter() - t_llm_start

        t_parse_start = time.perf_counter()
        # 兜底解析：仅处理流式阶段未捕获的剩余条目
        remaining_ids = expected_keys - _stream_translations.keys()
        fallback_translations = self._builder.parse_translation_response(response, remaining_ids)
        t_parse_elapsed = time.perf_counter() - t_parse_start

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
            # 仅写回兜底阶段的结果（流式阶段已写回）
            success = self._accept_candidates(fallback_translations, collection, result, lock)
            if id_to_translation:
                auto_term_entries = [
                    e for e in llm_entries
                    if (e.context.split("|")[0] if "|" in (e.context or "") else e.context) in AUTO_TERM_CONTEXTS
                ]
                if auto_term_entries:
                    self._update_dynamic_terms(auto_term_entries, id_to_translation, result, lock)
                if batch.batch_type == "对话":
                    self._extract_dialogue_terms(llm_entries, id_to_translation, result, lock)
            for half in halves:
                sub = _Batch(entries=half, batch_type=batch.batch_type, quest_formid=batch.quest_formid)
                self._run_batch(sub, collection, result, lock, log_callback, pause_event, stop_event, stream_callback, _min_size, progress_emit=progress_emit)
            if _timing_out is not None:
                _timing_out.update({'t_terms': t_terms - t0, 't_llm': t_llm_elapsed, 't_parse': t_parse_elapsed})
            return success + len(direct_fill)

        if missing:
            _log(f"  ⚠ {len(missing)} 条未获得译文（已缩至单条，无法继续拆分）")

        # 仅写回兜底阶段的结果（流式阶段已写回）
        success = self._accept_candidates(fallback_translations, collection, result, lock)

        # 自动写入动态术语库（仅 context 属于 AUTO_TERM_CONTEXTS 的条目，直接填充的来源已在术语库中）
        auto_term_entries = [
            e for e in llm_entries
            if (e.context.split("|")[0] if "|" in (e.context or "") else e.context) in AUTO_TERM_CONTEXTS
        ]
        if auto_term_entries:
            self._update_dynamic_terms(auto_term_entries, id_to_translation, result, lock)

        # 从对话批次抽取专有名词
        if batch.batch_type == "对话" and id_to_translation:
            self._extract_dialogue_terms(llm_entries, id_to_translation, result, lock)

        if _timing_out is not None:
            _timing_out.update({'t_terms': t_terms - t0, 't_llm': t_llm_elapsed, 't_parse': t_parse_elapsed})
        return success + len(direct_fill)

    def _accept_candidates(
        self,
        id_to_translation: dict[str, str],
        collection: "TranslationEntryCollection",
        result: TranslationResult,
        lock: threading.Lock,
    ) -> int:
        if self._candidate_session is None:
            raise RuntimeError("translation candidate session was not initialized")
        with lock:
            accepted = self._candidate_session.accept(id_to_translation, collection)
            result.success_count += accepted.accepted
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
    ) -> None:
        translated_pairs = [
            {"original": e.original, "translation": id_to_translation[e.id]}
            for e in entries if e.id in id_to_translation
        ]
        if not translated_pairs:
            return
        extracted = self._extractor.extract(translated_pairs)
        if extracted:
            # 过滤掉所有来源（dynamic/paratranz/json/excel）中已存在的术语，保留已有译名
            new_terms = [te for te in extracted if not self._term_mgr.has_term(te.term)]
            if new_terms:
                terms = [(te.term, te.translation, te.source, "") for te in new_terms]
                self._term_mgr.get_dynamic_db().add_many_and_save(terms)
                with lock:
                    result.new_dynamic_terms += len(new_terms)
