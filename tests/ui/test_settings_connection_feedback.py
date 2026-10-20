"""Connection feedback distinguishes configured credentials, progress and verification."""

import threading
import time
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton
import pytest

from tests.ui.responsive_layout_support import themed_layout_app as themed_layout_app
from tests.ui.test_ui_settings_dialog import _dialog, _preferences
from transbridge.config.llm import LLMConfig
from transbridge.ui.settings import center_layout, connection_checks
from transbridge.ui.settings.connection_checks import SettingsConnectionController
from transbridge.ui.settings.draft import ParaTranzSettingsDraft
from transbridge.ui.settings.paratranz_page import ParaTranzSettingsPage
from transbridge.ui.settings.sections import SettingsSection
from transbridge.ui.tools.ai_translator.config_presenter import ConnectionTestResult


@pytest.fixture(scope="module", autouse=True)
def connection_app():
    # ApiWorker buses belong to the QApplication; retain it across parametrized cases.
    return QApplication.instance() or QApplication([])


def _wait(app, predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    assert predicate()


@pytest.mark.parametrize("configured", [False, True])
def test_credentials_do_not_claim_a_verified_connection(layout_app, configured):
    page = ParaTranzSettingsPage(
        ParaTranzSettingsDraft("https://example.invalid", 30, None), token_configured=configured
    )
    try:
        assert "已连接" not in page.status_label.text()
        assert "验证" in page.test_status.text()
        assert page.status_label.property("tbStatusId") != "success"
        assert page.status_label.accessibleDescription() == page.status_label.text()
    finally:
        page.close()


@pytest.mark.parametrize(
    "result,expected",
    [
        (ConnectionTestResult("warning", "配置不完整", "请先填写 API Key。"), "warning"),
        (ConnectionTestResult("info", "语义检索已关闭", "当前使用字面匹配。"), "info"),
        (ConnectionTestResult("success", "验证成功", "连接可用。"), "success"),
        (ConnectionTestResult("critical", "失败", "sensitive exception detail"), "error"),
    ],
)
def test_background_result_and_retry_have_accurate_visible_and_accessible_states(layout_app, result, expected):
    button = QPushButton("测试连接")
    status = QLabel("旧结果")
    release = threading.Event()

    def operation():
        assert release.wait(3)
        return result

    controller = SettingsConnectionController(button, status, lambda: operation, idle_text="测试连接")
    status.setProperty("tbStatusId", "error")
    try:
        for _attempt in range(2):
            release.clear()
            assert controller.start()
            assert not controller.start()
            assert status.property("tbStatusId") == "running"
            assert status.property("tbSemanticState") == "info"
            assert "正在" in status.text() and status.accessibleDescription() == status.text()
            assert not button.isEnabled()
            release.set()
            _wait(layout_app, lambda: not controller.is_running)
            assert button.isEnabled()
            assert status.property("tbStatusId") == expected
            assert status.property("tbSemanticState") == expected
            assert status.accessibleDescription() == status.text()
            assert "sensitive" not in status.text()
    finally:
        release.set()
        controller.close()
        _wait(layout_app, lambda: not connection_checks._ACTIVE_WORKERS)
        button.close()
        status.close()


def test_success_adapters_keep_disabled_and_missing_configuration_neutral(monkeypatch):
    monkeypatch.setattr(
        connection_checks, "check_llm_connection", lambda _config: ConnectionTestResult("info", "成功", "已收到回复")
    )
    assert connection_checks.llm_operation(SimpleNamespace())().level == "success"
    monkeypatch.setattr(
        connection_checks,
        "check_llm_connection",
        lambda _config: ConnectionTestResult("warning", "配置不完整", "缺少模型"),
    )
    assert connection_checks.llm_operation(SimpleNamespace())().level == "warning"
    assert connection_checks.check_embedding_connection(SimpleNamespace()).level == "info"


def test_embedding_and_paratranz_only_report_success_after_a_completed_check(monkeypatch):
    from transbridge.infra import embedding_client
    from transbridge.paratranz.api import paratranz_user_api

    client = SimpleNamespace(available=True, encode=lambda _texts: SimpleNamespace(shape=(1, 384)))
    monkeypatch.setattr(embedding_client, "create_embedding_client", lambda _config: client)
    config = SimpleNamespace(embedding=SimpleNamespace(mode="local"))
    assert connection_checks.check_embedding_connection(config).level == "success"
    client.available = False
    assert connection_checks.check_embedding_connection(config).level == "critical"

    class AccountClient:
        closed = False

        def __init__(self, **_kwargs):
            pass

        def get_my_user(self):
            return {"nickname": "示例账户"}

        def close(self):
            AccountClient.closed = True

    monkeypatch.setattr(paratranz_user_api, "ParatranzUserAPI", AccountClient)
    result = connection_checks.paratranz_operation(
        SimpleNamespace(token="synthetic-token"), "https://example.invalid", 30, ""
    )()
    assert result.level == "success" and "示例账户" in result.message
    assert AccountClient.closed


@pytest.mark.parametrize("section", [SettingsSection.AI_SERVICE, SettingsSection.EMBEDDING, SettingsSection.PARATRANZ])
@pytest.mark.parametrize("late_error", [False, True])
def test_editing_connection_fields_invalidates_results_and_ignores_late_replies(
    layout_app, tmp_path, monkeypatch, section, late_error
):
    release = threading.Event()
    fail_check = False

    def operation():
        assert release.wait(10)
        if fail_check:
            raise RuntimeError("synthetic stale error")
        return ConnectionTestResult("success", "验证成功", "当前测试配置连接可用。")

    for name in ("llm_operation", "embedding_operation", "paratranz_operation"):
        monkeypatch.setattr(center_layout, name, lambda *_args: operation)
    service, dialog = _dialog(
        layout_app,
        _preferences(tmp_path),
        llm_config=LLMConfig(),
        paratranz_config=SimpleNamespace(token="synthetic-token", base_url="https://example.invalid", timeout=30),
        initial_section=section,
    )
    page = dialog._settings_pages[section]
    controller = next(control for control in dialog._connection_controllers if control._button is page.test_button)
    editors = (
        (page.base_url_edit, page.token_edit)
        if section is SettingsSection.PARATRANZ
        else (page.base_url_edit, page.model_edit, page.api_key_edit)
    )

    def edit_text(editor, value):
        editor.setFocus()
        editor.selectAll()
        for character in value:
            assert layout_app.focusWidget() is editor
            QTest.keyClicks(layout_app.focusWidget(), character)
            layout_app.processEvents()
        assert layout_app.focusWidget() is editor
        assert editor.text() == value

    def finish_check():
        editor = layout_app.focusWidget()
        editing = editor in editors
        if editing:
            editor.setSelection(1, 3)
            text = editor.text()
        release.set()
        _wait(layout_app, lambda: not controller.is_running)
        if editing:
            assert layout_app.focusWidget() is editor
            assert editor.text() == text
            assert editor.cursorPosition() == 4
            assert editor.selectionStart() == 1
            assert editor.selectedText() == text[1:4]

    def assert_pending():
        assert page.test_status.property("tbStatusId") == "pending"
        assert page.test_status.property("tbSemanticState") == "info"
        assert "重新验证" in page.test_status.text()
        assert page.test_status.accessibleDescription() == page.test_status.text()

    try:
        dialog.resize(720, 480)
        dialog.show()
        dialog.activateWindow()
        layout_app.processEvents()
        if section is SettingsSection.EMBEDDING:
            page.mode_combo.setCurrentIndex(page.mode_combo.findData("api"))
        assert controller.start()
        finish_check()
        assert page.test_status.property("tbStatusId") == "success"
        edit_text(page.base_url_edit, "https://changed.invalid")
        assert_pending()

        release.clear()
        fail_check = late_error
        assert controller.start()
        assert page.test_status.property("tbStatusId") == "running"
        if section is SettingsSection.PARATRANZ:
            edit_text(page.token_edit, "changed-synthetic-token")
            page.timeout_spin.setValue(42)
        else:
            edit_text(page.model_edit, "changed-model")
            edit_text(page.api_key_edit, "changed-synthetic-key")
        assert_pending()
        assert not page.test_button.isEnabled()
        finish_check()
        assert_pending()
        assert page.test_button.isEnabled()
        fail_check = False
        assert controller.start()
        finish_check()
        assert page.test_status.property("tbStatusId") == "success"

        if section is SettingsSection.PARATRANZ:
            page.disconnect_check.click()
        else:
            combo = page.mode_combo if section is SettingsSection.EMBEDDING else page.provider_combo
            combo.setFocus()
            QTest.keyClick(combo, Qt.Key.Key_Up if section is SettingsSection.EMBEDDING else Qt.Key.Key_Down)
        assert_pending()
        assert dialog._apply_button.property("tbSemanticState") == "primary"
    finally:
        release.set()
        dialog.reject()
        _wait(layout_app, lambda: not connection_checks._ACTIVE_WORKERS)
        service.close()
