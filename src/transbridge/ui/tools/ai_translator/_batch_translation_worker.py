"""
批量翻译后台线程。

依次翻译多个插件，支持暂停/停止/断点续传。
插件间实时共享新发现的术语。
"""

from __future__ import annotations

import logging
import os
import threading
import traceback
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PyQt6.QtCore import QThread, pyqtSignal

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from transbridge.ui.context import CollectionSlot
    from transbridge.paratranz.config_manager import LLMConfig
    from transbridge.ai_translator.translator import TranslationResult


@dataclass
class PluginTranslationResult:
    """单个插件的翻译结果。"""
    plugin_name: str
    success: bool
    result: "TranslationResult | None" = None
    error: str | None = None


@dataclass
class BatchTranslationSummary:
    """批量翻译总结。"""
    total_plugins: int
    success_plugins: int
    failed_plugins: int
    total_success_entries: int
    total_failed_entries: int
    details: list[PluginTranslationResult] = field(default_factory=list)


class _BatchTranslationWorker(QThread):
    """批量翻译后台线程：依次翻译多个 slot。"""

    # 插件开始：插件名、当前索引、总数
    plugin_started = pyqtSignal(str, int, int)
    # 插件完成：插件名、结果
    plugin_finished = pyqtSignal(str, object)
    # 插件翻译进度：当前条目、总条目、消息、成功数、失败数、新术语数
    plugin_progress = pyqtSignal(int, int, str, int, int, int)
    # 日志：batch_idx, line
    log = pyqtSignal(int, str)
    # 全部完成
    all_finished = pyqtSignal(object)  # BatchTranslationSummary
    # 错误
    error = pyqtSignal(str)

    def __init__(
        self,
        slots: list["CollectionSlot"],
        llm_config: "LLMConfig",
        overwrite: bool,
        paratranz_client=None,
        project_id: int | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._slots = slots
        self._llm_config = llm_config
        self._overwrite = overwrite
        self._paratranz_client = paratranz_client
        self._project_id = project_id

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # 初始为运行状态

        self._current_slot: "CollectionSlot | None" = None
        self._stream_log_dir: str | None = None

        # 共享的 in-flight 术语缓存：插件间实时共享新发现的术语
        self._shared_in_flight_terms: dict[str, str] = {}
        self._shared_in_flight_lock = threading.Lock()

    @property
    def stream_log_dir(self) -> str | None:
        """LLM 流式响应日志目录。"""
        return self._stream_log_dir

    def stop(self):
        """停止翻译。"""
        self._stop_event.set()
        self._pause_event.set()  # 确保 wait() 不阻塞

    def pause(self):
        """暂停翻译。"""
        self._pause_event.clear()

    def resume(self):
        """恢复翻译。"""
        self._pause_event.set()

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def _make_stream_log_dir(self) -> str:
        """创建日志目录。"""
        from transbridge.paratranz.config_manager import ParatranzConfig
        log_base = os.path.join(ParatranzConfig.get_data_dir(), "log")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = os.path.join(log_base, f"batch_{timestamp}")
        os.makedirs(log_dir, exist_ok=True)
        return log_dir

    def run(self):
        """执行批量翻译。"""
        self._stream_log_dir = self._make_stream_log_dir()

        summary = BatchTranslationSummary(
            total_plugins=len(self._slots),
            success_plugins=0,
            failed_plugins=0,
            total_success_entries=0,
            total_failed_entries=0,
        )

        try:
            for i, slot in enumerate(self._slots):
                if self._stop_event.is_set():
                    break

                # 等待暂停恢复
                self._pause_event.wait()

                if self._stop_event.is_set():
                    break

                self._current_slot = slot
                plugin_name = slot.label or Path(slot.esp_path or "").stem

                # 发出插件开始信号
                self.plugin_started.emit(plugin_name, i + 1, len(self._slots))

                # 执行单个插件翻译
                result = self._translate_single_slot(slot, plugin_name)

                # 记录结果
                plugin_result = PluginTranslationResult(
                    plugin_name=plugin_name,
                    success=result is not None,
                    result=result,
                )

                if result:
                    summary.success_plugins += 1
                    summary.total_success_entries += result.success_count
                    summary.total_failed_entries += result.failed_count
                else:
                    summary.failed_plugins += 1
                    plugin_result.error = "翻译失败或被中断"

                summary.details.append(plugin_result)
                self.plugin_finished.emit(plugin_name, result)

            # 全部完成
            self.all_finished.emit(summary)

        except Exception as exc:
            _logger.error("批量翻译Worker异常:\n%s", traceback.format_exc())
            self.error.emit(str(exc))

    def _translate_single_slot(
        self,
        slot: "CollectionSlot",
        plugin_name: str,
    ) -> "TranslationResult | None":
        """翻译单个插件。"""
        from transbridge.ai_translator.translator import (
            AutoTranslator, TranslatorConfig, ProgressCheckpoint, TranslationResult
        )

        if not slot.collection or not slot.esp_path:
            return None

        # 创建翻译配置
        translator_cfg = TranslatorConfig(
            llm_config=self._llm_config,
            esp_path=slot.esp_path,
            overwrite=self._overwrite,
        )

        # 创建翻译器（使用共享的 in-flight 缓存）
        translator = AutoTranslator(
            translator_cfg,
            self._paratranz_client,
            self._project_id,
            shared_in_flight_terms=self._shared_in_flight_terms,
            shared_in_flight_lock=self._shared_in_flight_lock,
        )

        # 检查断点
        checkpoint = ProgressCheckpoint.load(slot.esp_path)

        # 确定目标条目
        if self._overwrite:
            target_ids = None  # 全部翻译
        else:
            # 仅翻译未翻译的条目
            target_ids = [
                e.key for e in slot.collection
                if not e.translation or e.stage == 0
            ]
            if not target_ids:
                # 无需翻译
                return TranslationResult(success_count=0, skipped_count=len(slot.collection))

        # 日志目录
        plugin_log_dir = os.path.join(self._stream_log_dir, plugin_name)
        os.makedirs(plugin_log_dir, exist_ok=True)

        _file_handles: dict[int, object] = {}

        def _stream_cb(batch_idx: int, chunk: str):
            if batch_idx not in _file_handles:
                path = os.path.join(plugin_log_dir, f"batch_{batch_idx:03d}.log")
                _file_handles[batch_idx] = open(path, "w", encoding="utf-8")
            fh = _file_handles[batch_idx]
            fh.write(chunk)
            fh.flush()

        try:
            result = translator.translate(
                collection=slot.collection,
                target_entry_ids=target_ids,
                progress_callback=lambda c, t, m, s, f, n: self.plugin_progress.emit(c, t, m, s, f, n),
                stop_event=self._stop_event,
                pause_event=self._pause_event,
                checkpoint=checkpoint,
                log_callback=lambda idx, line: self.log.emit(idx, line),
                stream_callback=_stream_cb,
            )
            for fh in _file_handles.values():
                fh.close()
            return result

        except Exception as e:
            for fh in _file_handles.values():
                fh.close()
            import traceback
            err_msg = f"❌ 插件 {plugin_name} 翻译异常: {e}"
            self.log.emit(-1, err_msg)
            self.log.emit(-1, traceback.format_exc())
            # 返回 None 表示失败，由调用方记录错误
            return None
