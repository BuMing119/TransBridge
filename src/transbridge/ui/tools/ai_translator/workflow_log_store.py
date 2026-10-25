"""Thread-safe persisted logs for AI workflow progress windows."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import re

from transbridge.config.llm import DEFAULT_MAX_CONCURRENT, MAX_CONCURRENT
from transbridge.paratranz.config_manager import ParatranzConfig

from .workflow_log_writer import WorkflowLogWriter

_logger = logging.getLogger(__name__)


class WorkflowLogStore:
    """Persist raw LLM chunks and stage diagnostics in one run directory."""

    MAX_OPEN_FILES = MAX_CONCURRENT * 2 + 16  # Two channels per supported request, plus diagnostics.

    def __init__(
        self,
        esp_path: str | None,
        *,
        workflow: str,
        log_base: str | Path | None = None,
        log_dir: str | Path | None = None,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        source = self._safe_name(Path(esp_path).stem if esp_path else "translation")
        workflow_name = self._safe_name(workflow)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self._writer: WorkflowLogWriter | None = None
        self._last_error = ""
        try:
            if log_dir is not None:
                self.log_dir = str(Path(log_dir))
            else:
                base = Path(log_base) if log_base is not None else Path(ParatranzConfig.get_data_dir()) / "log"
                self.log_dir = str(base / f"{source}_{workflow_name}_{timestamp}")
            Path(self.log_dir).mkdir(parents=True, exist_ok=True)
            self._writer = WorkflowLogWriter(
                Path(self.log_dir), max_open_files=min(self.MAX_OPEN_FILES, max(16, max_concurrent * 2 + 16))
            )
        except OSError as exc:
            self.log_dir = ""
            self._last_error = str(exc)
            _logger.warning("AI 工作流日志已停用 (%s): %s", self.log_dir, exc)

    @property
    def is_available(self) -> bool:
        return self._writer is not None and not self._writer.last_error

    @property
    def last_error(self) -> str:
        return self._last_error or (self._writer.last_error if self._writer else "")

    def write_chunk(self, channel: str, chunk: str) -> None:
        if chunk and self._writer is not None:
            self._writer.write(self._safe_name(channel), chunk)

    def write_line(self, channel: str, line: str) -> None:
        if line:
            self.write_chunk(channel, f"{line}\n")

    def close_channel(self, channel: str) -> None:
        """Release a finished call; subsequent grouped/stream writes append safely."""
        if self._writer is not None:
            self._writer.close_channel(self._safe_name(channel))

    def flush(self) -> None:
        """Wait for preceding logs to be visible; call from a background owner."""
        if self._writer is not None:
            self._writer.flush()

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "workflow"


__all__ = ["WorkflowLogStore"]
