"""Refresh the assistant's public model summary on window activation."""

import logging

from PyQt6.QtCore import QEvent, QObject

from transbridge.smart_assistant.service_readiness import read_service_availability

logger = logging.getLogger(__name__)


def configured_model_name() -> str:
    """Read the public identifier without loading credentials into presentation."""
    try:
        from transbridge.config.repository import default_config_repository

        model = default_config_repository().load().value("llm", "model", "")
    except Exception:
        logger.warning("读取智能助手模型名称失败", exc_info=True)
        return "模型未配置"
    return str(model).strip() or "模型未配置"


class AssistantWindowPresentation(QObject):
    def __init__(self, panel, header, read_availability=read_service_availability):
        super().__init__(panel)
        self._panel = panel
        self._header = header
        self._read_availability = read_availability
        panel.installEventFilter(self)
        self.refresh()

    def refresh(self):
        if not self._panel.is_disposed:
            self._header.set_service_availability(self._read_availability())
        return self._header.service_availability

    def eventFilter(self, watched, event):
        if watched is self._panel and event.type() in (QEvent.Type.Show, QEvent.Type.WindowActivate):
            self.refresh()
        return super().eventFilter(watched, event)
