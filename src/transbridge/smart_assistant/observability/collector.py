import atexit
from collections.abc import Callable
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import threading

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

    def start_conversation(self, conv_id: str) -> None:
        if self._active is not None:
            self.end_conversation()
        self._active = ConversationTrace(conv_id=conv_id)
        # m12: 新会话重置 session 级 token 统计
        self._session_tokens = TokenStats()

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

    def end_conversation(self) -> ConversationTrace | None:
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
        for f in self._storage_dir.glob("*.json"):
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
