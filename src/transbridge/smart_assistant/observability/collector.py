import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from .models import ConversationTrace, ReActRound, ToolCallRecord, TokenStats
from ..execution_engine import StepResult

logger = logging.getLogger(__name__)


class ObservabilityCollector(QObject):
    token_stats_updated = pyqtSignal(object)

    def __init__(self, storage_dir: Path | None = None, parent=None):
        super().__init__(parent)
        self._storage_dir = storage_dir
        self._active: ConversationTrace | None = None
        self._session_tokens = TokenStats()
        self._current_round: ReActRound | None = None
        self._pending_tool: tuple | None = None
        self._round_start: datetime | None = None

    def start_conversation(self, conv_id: str) -> None:
        if self._active is not None:
            self.end_conversation()
        self._active = ConversationTrace(conv_id=conv_id)

    def on_step_started(self, step_id: int, tool_name: str) -> None:
        self._pending_tool = (step_id, tool_name, datetime.now())

    def on_step_finished(self, result: StepResult) -> None:
        if self._active is None:
            return
        if self._pending_tool:
            _sid, _tname, start_time = self._pending_tool
            self._active.tools_called.append(ToolCallRecord(
                timestamp=start_time.isoformat(),
                tool_name=_tname,
                input_summary=str(result.data)[:500] if result.data else "",
                output_summary=result.message[:500],
                duration_ms=result.duration_ms,
                success=result.success,
                retry_count=0,
            ))
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
        self.token_stats_updated.emit(self._session_tokens)

    def end_conversation(self) -> ConversationTrace | None:
        if self._active is None:
            return None
        self._active.finished_at = datetime.now().isoformat()
        trace = self._active
        self._active = None
        if self._storage_dir:
            try:
                self._save_trace(trace)
                self._cleanup_old()
            except Exception as exc:
                logger.warning("观测数据保存失败: %s", exc)
        return trace

    def _save_trace(self, trace: ConversationTrace) -> None:
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        path = self._storage_dir / f"{trace.conv_id}.json"
        path.write_text(json.dumps(trace.to_dict(), ensure_ascii=False, indent=2))

    def _cleanup_old(self, max_age_days: int = 30) -> None:
        if not self._storage_dir or not self._storage_dir.exists():
            return
        cutoff = datetime.now() - timedelta(days=max_age_days)
        for f in self._storage_dir.glob("*.json"):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime < cutoff:
                    f.unlink()
            except Exception:
                pass
