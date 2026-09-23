"""Explain skipped dependent work without disguising independent ready work as waiting."""

from PyQt6.QtWidgets import QApplication

from transbridge.application.assistant_requests.models import ItemStatus, RequestItem, UserRequest
from transbridge.application.assistant_requests.reducer import converge
from transbridge.ui.tools.smart_assistant.request_list_view import RequestListView


def test_dependency_failures_are_visible_without_false_wait_label():
    app = QApplication.instance() or QApplication([])
    view = RequestListView()
    request = converge(
        UserRequest(
            "r",
            "s",
            "translate and export",
            (
                RequestItem("a", "translate", status=ItemStatus.FAILED),
                RequestItem("b", "export", dependencies=("a",)),
                RequestItem("c", "independent report"),
            ),
        )
    )
    try:
        view.display((request,))
        text = view.items.item(0).text()
        assert "1项未执行：前置事项失败" in text
        assert "等待处理" not in text
        assert "待完成" in text
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()
