"""Thread-safe persisted logs for AI workflow progress windows."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import re
import threading
from typing import TextIO

from transbridge.paratranz.config_manager import ParatranzConfig

_logger = logging.getLogger(__name__)


class WorkflowLogStore:
    """Persist raw LLM chunks and stage diagnostics in one run directory."""

    def __init__(
        self,
        esp_path: str | None,
        *,
        workflow: str,
        log_base: str | Path | None = None,
        log_dir: str | Path | None = None,
    ) -> None:
        source = self._safe_name(Path(esp_path).stem if esp_path else "translation")
        workflow_name = self._safe_name(workflow)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self._handles: dict[str, TextIO] = {}
        self._lock = threading.Lock()
        self._available = True
        self._closed = False
        self._last_error = ""
        try:
            if log_dir is not None:
                self.log_dir = str(Path(log_dir))
            else:
                base = Path(log_base) if log_base is not None else Path(ParatranzConfig.get_data_dir()) / "log"
                self.log_dir = str(base / f"{source}_{workflow_name}_{timestamp}")
            Path(self.log_dir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.log_dir = ""
            self._disable(exc)

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def last_error(self) -> str:
        return self._last_error

    def write_chunk(self, channel: str, chunk: str) -> None:
        if not chunk:
            return
        name = self._safe_name(channel)
        with self._lock:
            if not self._available or self._closed:
                return
            try:
                handle = self._handles.get(name)
                if handle is None:
                    handle = (Path(self.log_dir) / f"{name}.log").open("w", encoding="utf-8")
                    self._handles[name] = handle
                handle.write(chunk)
                handle.flush()
            except (OSError, ValueError) as exc:
                self._disable(exc)

    def write_line(self, channel: str, line: str) -> None:
        if line:
            self.write_chunk(channel, f"{line}\n")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            for handle in self._handles.values():
                try:
                    handle.close()
                except OSError as exc:
                    _logger.warning("关闭 AI 工作流日志失败 (%s): %s", self.log_dir, exc)
            self._handles.clear()
            self._closed = True

    def _disable(self, exc: Exception) -> None:
        self._last_error = str(exc)
        if self._available:
            _logger.warning("AI 工作流日志已停用 (%s): %s", self.log_dir, exc)
        self._available = False

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "workflow"


__all__ = ["WorkflowLogStore"]
