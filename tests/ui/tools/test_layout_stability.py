from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication, QSizePolicy, QVBoxLayout, QWidget
import pytest

from tests.ui.tools.ai_read_projection_support import wait_for
from transbridge.ui.tools.ai_translator._llm_log_viewer import _LLMLogViewer
from transbridge.ui.tools.ai_translator._translation_progress_window import _TranslationProgressWindow
from transbridge.ui.tools.ai_translator.config_view import AITranslatorView
from transbridge.ui.tools.ai_translator.run_view import AiMixedProgressWindow
from transbridge.ui.tools.smart_assistant.composer_editor import ComposerEditor
from transbridge.ui.tools.smart_assistant.input_view import ChatInputView
from transbridge.ui.tools.smart_assistant.task_monitor import _TaskCard
from transbridge.ui.tools.smart_assistant.tool_card import ToolCard


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _assert_horizontal_stable(qapp, widget, action_widget, mutate, *, width: int) -> None:
    widget.resize(width, max(300, widget.sizeHint().height()))
    widget.show()
    qapp.processEvents()
    before = (widget.minimumSizeHint().width(), action_widget.x(), action_widget.width())

    mutate()
    qapp.processEvents()

    assert (widget.minimumSizeHint().width(), action_widget.x(), action_widget.width()) == before
    widget.close()


class _Callbacks:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class _TranslationWorker(QObject):
    progress = pyqtSignal(int, int, str, int, int, int)
    log = pyqtSignal(int, str)
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def isRunning(self) -> bool:  # noqa: N802 - Qt compatibility
        return False


class _MixedWorker(QObject):
    progress = pyqtSignal(object)
    completed = pyqtSignal(object)
    error = pyqtSignal(str)
    cancelled = pyqtSignal()

    def isRunning(self) -> bool:  # noqa: N802 - Qt compatibility
        return False

    def cancel(self) -> None:
        pass


class _Activity:
    task_activity = None

    def request_cancel(self) -> None:
        pass


def test_output_token_zero_label_is_conditional_on_provider_support(qapp) -> None:
    parent = QWidget()
    view = AITranslatorView(parent, _Callbacks())

    view._output_tokens_spin.setValue(0)

    assert view._output_tokens_spin.specialValueText() == "不限制（供应商支持时）"
    parent.close()


def test_ai_footer_long_reason_does_not_move_start_button(qapp) -> None:
    parent = QWidget()
    view = AITranslatorView(parent, _Callbacks())
    text = "运行条件失败：" + "超长原因" * 200

    def mutate() -> None:
        view.controls.preflight_label.set_full_text(text)
        view.controls.preflight_label.setToolTip(text)
        view.controls.preflight_label.setAccessibleDescription(text)

    _assert_horizontal_stable(qapp, parent, view.controls.start_btn, mutate, width=680)
    assert view.controls.preflight_label.toolTip() == text
    assert view.controls.preflight_label.accessibleDescription() == text


def test_ai_scope_estimates_wrap_inside_horizontal_scroll_free_view(qapp) -> None:
    parent = QWidget()
    view = AITranslatorView(parent, _Callbacks())

    for label in (view.controls.estimate_lbl, view.controls.mixed_estimate_lbl):
        label.setText("预计：" + "很长的完整范围估算；" * 100)
        assert label.wordWrap()
        assert label.sizePolicy().horizontalPolicy() is QSizePolicy.Policy.Ignored
        assert label.heightForWidth(320) > label.fontMetrics().height()

    parent.close()


def test_smart_assistant_dynamic_text_keeps_action_columns_stable(qapp) -> None:
    def noop(*_args, **_kwargs) -> None:
        pass

    host = QWidget()
    layout = QVBoxLayout(host)
    input_view = ChatInputView(
        set_input=noop,
        select_skill=noop,
        upload=noop,
        clear=noop,
        send=noop,
        toggle_auto=noop,
        auto_mode=False,
    )
    input_view.build_toolbar(layout)
    upload_text = "已上传 50 个: " + ", ".join(f"参考文件-{index}-{'x' * 80}.md" for index in range(50))
    _assert_horizontal_stable(
        qapp,
        host,
        input_view._upload_button,
        lambda: input_view.upload_label.set_full_text(upload_text),
        width=900,
    )

    card = ToolCard({"tool": "translate"})
    result = "工具失败：" + "详细错误" * 200
    _assert_horizontal_stable(qapp, card, card._exec_btn, lambda: card.set_result(False, result), width=500)
    assert card._result_label.toolTip().endswith(result)
    assert result in card._result_label.accessibleDescription()

    task = _TaskCard(
        "task-1",
        {"status": "running", "progress": {"message": "准备中"}, "metadata": {"name": "普通任务"}},
    )
    task_name = "后台任务-" + "很长的任务名称" * 200
    _assert_horizontal_stable(
        qapp,
        task,
        task._action_buttons[0][0],
        lambda: task._name_label.set_full_text(task_name),
        width=900,
    )


@pytest.mark.parametrize("font_size", (9, 14))
@pytest.mark.parametrize(
    "transition", ("execute", "ignore", "expire", "interrupt", "success", "failure", "cancelled", "started", "unknown")
)
def test_tool_card_status_changes_keep_action_geometry_with_cjk_font(qapp, font_size, transition) -> None:
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if not font_path.exists():
        pytest.skip("This regression requires the Windows Chinese font used by the desktop application")
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id < 0:
        pytest.skip("The Chinese font could not be loaded")
    previous_font = QFont(qapp.font())
    card = None
    try:
        qapp.setFont(QFont("Microsoft YaHei", font_size))
        card = ToolCard({"tool": "translate"})
        change = {
            "execute": card._on_execute,
            "ignore": card._on_ignore,
            "expire": card.expire,
            "interrupt": card.interrupt,
            "success": lambda: card.set_result(True, "完成"),
            "failure": lambda: card.set_result(False, "工具失败：" + "详细错误" * 200),
            "cancelled": lambda: card.set_result(False, "已取消", status="cancelled"),
            "started": lambda: card.set_result(True, "任务已启动", status="started"),
            "unknown": lambda: card.set_result(False, "结果待核对", status="unknown"),
        }[transition]
        _assert_horizontal_stable(qapp, card, card._exec_btn, change, width=500)
        assert card._exec_btn.width() >= card._exec_btn.sizeHint().width()
    finally:
        if card is not None:
            card.close()
            card.deleteLater()
        qapp.processEvents()
        qapp.setFont(previous_font)
        QFontDatabase.removeApplicationFont(font_id)


def test_smart_assistant_input_is_one_bounded_composer_card(qapp) -> None:
    def noop(*_args, **_kwargs) -> None:
        pass

    host = QWidget()
    layout = QVBoxLayout(host)
    input_view = ChatInputView(
        set_input=noop,
        select_skill=noop,
        upload=noop,
        clear=noop,
        send=noop,
        toggle_auto=noop,
        auto_mode=False,
    )
    input_view.build_toolbar(layout)
    input_view.build_editor(layout, host)

    try:
        assert layout.count() == 1
        assert layout.itemAt(0).widget() is input_view._card
        assert input_view.input.minimumHeight() == ComposerEditor.MINIMUM_HEIGHT
        assert input_view.input.maximumHeight() <= ComposerEditor.MAXIMUM_HEIGHT
        assert input_view.input.sizePolicy().verticalPolicy() is QSizePolicy.Policy.Preferred
        assert input_view.auto_checkbox.isCheckable()
        assert input_view.send_button.text() == "发送"
        assert input_view.send_button.minimumWidth() == input_view.send_button.maximumWidth() == 96
        assert not input_view.send_button.icon().isNull()

        host.resize(600, 400)
        host.show()
        qapp.processEvents()
        host_size = host.size()
        compact_height = input_view.input.height()
        send_size = input_view.send_button.size()
        assert compact_height == ComposerEditor.MINIMUM_HEIGHT
        assert input_view.send_button.isVisible()

        message = "long message\n" * 1000
        input_view.input.setPlainText(message)
        qapp.processEvents()

        assert compact_height < input_view.input.height() == ComposerEditor.MAXIMUM_HEIGHT
        assert input_view.input.verticalScrollBar().maximum() > 0
        assert input_view.input.toPlainText() == message
        assert host.size() == host_size
        assert input_view.send_button.size() == send_size
        assert input_view.send_button.isVisible()
        assert input_view._card.rect().contains(input_view.send_button.geometry())
        for point in (input_view.send_button.rect().topLeft(), input_view.send_button.rect().bottomRight()):
            assert host.rect().contains(input_view.send_button.mapTo(host, point))
    finally:
        input_view.close()
        host.close()
        host.deleteLater()


def test_log_viewer_long_path_keeps_toolbar_stable(qapp) -> None:
    short = _LLMLogViewer("C:/logs")
    long_path = "C:/" + "/very-long-directory" * 200
    long = _LLMLogViewer(long_path)
    short.resize(900, 640)
    long.resize(900, 640)
    short.show()
    long.show()
    qapp.processEvents()

    assert long.minimumSizeHint().width() == short.minimumSizeHint().width()
    assert long._refresh_btn.x() == short._refresh_btn.x()
    assert long._path_label.full_text == long_path
    assert long._path_label.toolTip() == long_path
    short.close()
    long.close()


def test_llm_log_viewer_refreshes_list_manually_and_loads_only_selection(qapp, tmp_path: Path) -> None:
    (tmp_path / "proofread_call_001.log").write_text("first log", encoding="utf-8")
    (tmp_path / "workflow.log").write_text("workflow log", encoding="utf-8")
    viewer = _LLMLogViewer(str(tmp_path))
    viewer.show()
    wait_for(lambda: viewer._text_edit.toPlainText() == "workflow log")

    assert viewer._log_selector.count() == 2
    assert viewer._text_edit.toPlainText() == "workflow log"

    viewer._log_selector.setCurrentIndex(0)
    wait_for(lambda: viewer._text_edit.toPlainText() == "first log")
    assert viewer._text_edit.toPlainText() == "first log"

    (tmp_path / "proofread_call_002.log").write_text("second log", encoding="utf-8")
    qapp.processEvents()
    assert viewer._log_selector.count() == 2

    viewer._refresh_btn.click()
    wait_for(lambda: viewer._log_selector.count() == 3)
    assert viewer._log_selector.count() == 3
    assert viewer._text_edit.toPlainText() == "first log"
    viewer.close()


def test_progress_statuses_do_not_change_window_or_action_geometry(qapp) -> None:
    single = _TranslationProgressWindow(_TranslationWorker(), SimpleNamespace())
    message = "翻译进度：" + "长状态" * 300
    _assert_horizontal_stable(
        qapp,
        single,
        single._llm_log_btn,
        lambda: single._on_progress(1, 10, message, 1, 0, 0),
        width=560,
    )
    assert single._progress_msg.toolTip() == message

    mixed = AiMixedProgressWindow(_MixedWorker(), _Activity())
    error = "失败：" + "服务错误" * 300
    _assert_horizontal_stable(qapp, mixed, mixed._stop, lambda: mixed._on_error(error), width=440)
    assert error in mixed._status.full_text
    assert mixed._status.toolTip() == mixed._status.full_text
