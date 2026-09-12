import atexit
from collections.abc import Callable
from datetime import datetime, timedelta
import hashlib
from itertools import chain
import json
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
import threading

from transbridge.infra.llm_usage import LlmUsage

from ..execution_engine import StepResult
from ..guardrails.output_validator import sanitize_for_storage
from .models import ConversationTrace, ReActRound, TokenStats, ToolCallRecord

logger = logging.getLogger(__name__)

# m9/m11: 单次清理最多扫描的文件数上限
_MAX_CLEANUP_FILES = 500

# M37: 守护线程保存的 trace 可能在进程退出时丢失，通过 atexit 兜底同步写入
_pending_traces: list[tuple[ConversationTrace, Path]] = []
_pending_lock = threading.Lock()


def _atexit_flush() -> None:
    """进程退出前同步写入仍在等待的 trace。"""
    with _pending_lock:
        for trace, storage_dir in _pending_traces:
            try:
                storage_dir.mkdir(parents=True, exist_ok=True)
                path = storage_dir / f"{trace.conv_id}.json"
                data = sanitize_for_storage(trace.to_dict())
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                logger.debug("atexit: flushed pending trace %s", trace.conv_id)
            except Exception:
                logger.warning("atexit: failed to flush trace %s", trace.conv_id)
        _pending_traces.clear()


atexit.register(_atexit_flush)


class ObservabilityCollector:
    """可观测性收集器 — 追踪对话轮次、工具调用、Token 统计。

    Phase 2: 移除 QObject/pyqtSignal 继承，token_stats_updated 改为回调注入。
    调用方通过 on_token_stats_updated 参数注册回调，跨线程安全由调用方保证。
    """

    _MAX_TRACE_AGE_DAYS = 30

    def __init__(self, storage_dir: Path | None = None, *, on_token_stats_updated: Callable | None = None):
        self._storage_dir = storage_dir
        self._on_token_stats_updated = on_token_stats_updated
        self._active: ConversationTrace | None = None
        self._session_tokens = TokenStats()
        self._current_round: ReActRound | None = None
        self._pending_tool: tuple | None = None
        self._round_start: datetime | None = None
        self._usage_lock = threading.RLock()

    def start_conversation(self, conv_id: str) -> None:
        with self._usage_lock:
            if self._active is not None:
                self.end_conversation()
            self._active = ConversationTrace(conv_id=conv_id)
            # m12: 新会话重置 session 级 token 统计
            self._session_tokens = TokenStats()
            self._current_round = None

    def on_step_started(self, step_id: int, tool_name: str) -> None:
        self._pending_tool = (step_id, tool_name, datetime.now())

    def on_step_finished(self, result: StepResult) -> None:
        if self._active is None:
            return
        if self._pending_tool:
            _sid, _tname, start_time = self._pending_tool
            self._active.tools_called.append(
                ToolCallRecord(
                    timestamp=start_time.isoformat(),
                    tool_name=_tname,
                    input_summary=str(result.data)[:500] if result.data else "",
                    output_summary=result.message[:500],
                    duration_ms=result.duration_ms,
                    success=result.success,
                    retry_count=0,
                )
            )
            self._pending_tool = None

    def on_step_retrying(self, step_id: int, attempt: int) -> None:
        if self._active and self._active.tools_called:
            self._active.tools_called[-1].retry_count = attempt

    def on_llm_tokens(self, model: str, input_tokens: int, output_tokens: int) -> None:
        if self._active:
            self._active.token_stats.add(model, input_tokens, output_tokens)
            if self._current_round:
                self._current_round.llm_input_tokens += input_tokens
        self._session_tokens.add(model, input_tokens, output_tokens)
        if self._on_token_stats_updated:
            self._on_token_stats_updated(self._session_tokens)

    def on_llm_usage(self, usage: LlmUsage) -> None:
        """Legacy immediate delivery. Async callers must capture attribution before dispatch."""
        self.capture_usage_callback(notify=lambda callback: callback())(usage)

    def capture_usage_callback(self, *, notify: Callable[[Callable[[], None]], None] | None = None) -> Callable:
        """Bind accounting to this invocation's trace, independent of the active view.

        ``notify`` schedules a zero-argument callback (for example a GUI dispatch
        signal's ``emit``). Without it, delivery only records diagnostics. Both
        scheduling and eventual notification are isolated from accounting.
        """
        with self._usage_lock:
            trace, stats, round_record = self._active, self._session_tokens, self._current_round

        def notify_current() -> None:
            try:
                with self._usage_lock:
                    if self._active is not trace or self._session_tokens is not stats:
                        return
                    if trace is not None and trace.finished_at:
                        return
                    if self._on_token_stats_updated:
                        self._on_token_stats_updated(stats)
            except Exception:
                logger.warning("Usage notification failed; accounting was retained", exc_info=True)

        def record(usage: LlmUsage) -> None:
            with self._usage_lock:
                if not stats.add_usage(usage):
                    return
                if trace is not None:
                    trace.token_stats.add_usage(usage)
                if round_record and usage.source == "reported" and usage.input_tokens is not None:
                    round_record.llm_input_tokens += usage.input_tokens
                current = self._active is trace and self._session_tokens is stats
            # This separate diagnostic ledger never opens the business session
            # repository or recreates a deleted session/trace. It also survives
            # same-ID conversation reopening without overwriting the newer trace.
            self._save_usage_attempt(trace, usage)
            if current and notify is not None:
                try:
                    notify(notify_current)
                except Exception:
                    logger.warning("Usage notification dispatch unavailable; accounting was retained", exc_info=True)

        return record

    def _save_usage_attempt(self, trace: ConversationTrace | None, usage: LlmUsage) -> None:
        if self._storage_dir is None:
            return
        temporary = None
        try:
            directory = self._storage_dir / "usage-attempts"
            directory.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(usage.attempt_id.encode("utf-8")).hexdigest()
            path = directory / f"{digest}.json"
            record = {
                "schema_version": 1,
                "conv_id": trace.conv_id if trace else None,
                "trace_started_at": trace.started_at if trace else None,
                "usage": usage.to_dict(),
            }
            data = json.dumps(sanitize_for_storage(record), ensure_ascii=False, indent=2)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, prefix=".usage-", delete=False) as file:
                temporary = Path(file.name)
                file.write(data)
            temporary.replace(path)
        except Exception:
            logger.warning(
                "Failed to persist usage attempt %s; in-memory accounting retained", usage.attempt_id, exc_info=True
            )
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Failed to remove owned usage staging file", exc_info=True)

    def end_conversation(self) -> ConversationTrace | None:
        with self._usage_lock:
            if self._active is None:
                return None
            self._active.finished_at = datetime.now().isoformat()
            trace = self._active
            self._active = None
        if self._storage_dir:
            try:
                # M37: 注册到 atexit 兜底列表，防止守护线程未完成时进程退出丢失数据
                with _pending_lock:
                    _pending_traces.append((trace, self._storage_dir))
                # M16: 将同步文件 I/O 包装在后台线程中异步执行
                threading.Thread(target=self._save_trace, args=(trace,), daemon=True).start()
                # M37: 将清理操作放到后台线程，避免阻塞调用方（UI）线程
                threading.Thread(target=self._cleanup_old, daemon=True).start()
            except Exception as exc:
                logger.warning("观测数据保存失败: %s", exc)
        # m15: 保存后清理活跃追踪（避免数据丢失）
        trace.tools_called.clear()
        return trace

    def _save_trace(self, trace: ConversationTrace) -> None:
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        path = self._storage_dir / f"{trace.conv_id}.json"
        # C5: 持久化前脱敏 — 移除 API 密钥/Token/文件路径等敏感信息
        data = sanitize_for_storage(trace.to_dict())
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        # M37: 写入成功后从 atexit 兜底列表移除，避免重复写入
        with _pending_lock:
            try:
                _pending_traces.remove((trace, self._storage_dir))
            except ValueError:
                pass  # 可能已被 atexit 线程消费

    def _cleanup_old(self, max_age_days: int | None = None) -> None:
        if max_age_days is None:
            max_age_days = self._MAX_TRACE_AGE_DAYS
        if not self._storage_dir or not self._storage_dir.exists():
            return
        cutoff = datetime.now() - timedelta(days=max_age_days)
        # m9/m11: 限制单次扫描文件数，防止大量文件累积时 IO 阻塞。
        # 当文件数超过上限时，采用滚动清理策略：按 mtime 排序后优先删除最旧文件，
        # 确保即使累积过多文件也能逐步清理，避免数据无限增长。
        scanned = 0
        expired_files: list[tuple[float, Path]] = []
        for f in chain(self._storage_dir.glob("*.json"), (self._storage_dir / "usage-attempts").glob("*.json")):
            try:
                mtime = f.stat().st_mtime
                if datetime.fromtimestamp(mtime) < cutoff:
                    expired_files.append((mtime, f))
            except OSError:
                pass
            scanned += 1
        if scanned >= _MAX_CLEANUP_FILES:
            logger.warning(
                "ObservabilityCollector: 追踪文件数已达上限 (%d)，"
                "启用滚动清理：按修改时间排序，优先删除最旧文件。"
                "当前过期文件数: %d",
                _MAX_CLEANUP_FILES,
                len(expired_files),
            )
        # 按修改时间升序排序，优先删除最旧文件
        expired_files.sort(key=lambda x: x[0])
        for _, f in expired_files:
            try:
                f.unlink()
            except OSError:
                pass  # 清理旧追踪文件失败不影响主流程
