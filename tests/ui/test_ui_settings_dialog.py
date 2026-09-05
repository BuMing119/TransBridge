from __future__ import annotations

import os
from pathlib import Path
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QWidget
import pytest

from transbridge.config.llm import LLMConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode, UiPreferenceRepository
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService
from transbridge.ui.settings import connection_checks as settings_connection_module
from transbridge.ui.settings.connection_checks import SettingsConnectionController
from transbridge.ui.settings.draft import SettingsConfigDraft
from transbridge.ui.settings.sections import SECTION_LABELS, SettingsSection
from transbridge.ui.settings_dialog import PersistFailureChoice, SettingsDialog
from transbridge.ui.tools.ai_translator.config_presenter import ConnectionTestResult


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _preferences(tmp_path: Path, *, replace_func=os.replace) -> UiPreferenceRepository:
    path = tmp_path / "ui.ini"
    return UiPreferenceRepository(
        ConfigRepository(
            path,
            legacy_path=path,
            credential_store=UnavailableCredentialStore(),
            replace_func=replace_func,
        )
    )


def _dialog(qapp, preferences, *, resolver=None, **kwargs):
    registry = create_builtin_registry()
    service = ThemeService(qapp, registry, preferences)
    service.start()
    dialog = SettingsDialog(
        service,
        preferences,
        registry=registry,
        persist_failure_resolver=resolver,
        **kwargs,
    )
    return service, dialog


def test_locale_service_translates_critical_settings_chrome(qapp, tmp_path: Path) -> None:
    class _Locale:
        @staticmethod
        def gettext(msgid: str) -> str:
            return f"译:{msgid}"

    preferences = _preferences(tmp_path)
    registry = create_builtin_registry()
    service = ThemeService(qapp, registry, preferences)
    service.start()
    dialog = SettingsDialog(service, preferences, registry=registry, locale_service=_Locale())

    assert dialog.windowTitle() == "译:通用设置"
    assert dialog._mode_combo.itemText(0) == "译:跟随系统"
    assert dialog._theme_combo.accessibleName() == "译:主题提供者"
    assert dialog._effective_scheme.text().startswith("译:")
    assert dialog._apply_button.text() == "译:应用"
    assert dialog._default_button.text() == "译:恢复默认外观"
    assert dialog._cancel_button.text() == "译:取消"
    dialog._show_notice(dialog._tr("所选主题当前不可用，请选择其他主题。"), error=True)
    assert dialog.last_notice == "译:所选主题当前不可用，请选择其他主题。"

    dialog.reject()
    service.close()


def test_preview_is_isolated_from_application_and_business_widgets(qapp, tmp_path: Path) -> None:
    original_palette = QPalette(qapp.palette())
    preferences = _preferences(tmp_path)
    preferences.save_theme_preference(ThemeMode.LIGHT, DEFAULT_THEME_ID)
    service, dialog = _dialog(qapp, preferences)
    business = QWidget()
    app_before = qapp.palette().color(QPalette.ColorRole.Window)
    business_before = business.palette().color(QPalette.ColorRole.Window)
    revision_before = service.snapshot().revision

    dialog.set_draft(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID))
    qapp.processEvents()

    assert dialog.preview.snapshot is not None
    assert dialog.preview.snapshot.effective_scheme.value == "dark"
    assert dialog.preview.palette().color(QPalette.ColorRole.Window) != app_before
    assert qapp.palette().color(QPalette.ColorRole.Window) == app_before
    assert business.palette().color(QPalette.ColorRole.Window) == business_before
    assert service.snapshot().revision == revision_before
    assert service.preference.mode is ThemeMode.LIGHT
    assert preferences.load().theme_mode is ThemeMode.LIGHT
    dialog.reject()
    assert dialog.preview.disposed
    service.close()
    business.deleteLater()
    qapp.setPalette(original_palette)


def test_apply_persists_and_updates_existing_and_new_widgets(qapp, tmp_path: Path) -> None:
    original_palette = QPalette(qapp.palette())
    preferences = _preferences(tmp_path)
    preferences.save_theme_preference(ThemeMode.LIGHT, DEFAULT_THEME_ID)
    service, dialog = _dialog(qapp, preferences)
    existing = QWidget()
    before = existing.palette().color(QPalette.ColorRole.Window)
    dialog.set_draft(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID))

    assert dialog.apply_draft()
    qapp.processEvents()
    created_after = QWidget()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.disposed
    assert service.preference.mode is ThemeMode.DARK
    assert preferences.load().theme_mode is ThemeMode.DARK
    assert existing.palette().color(QPalette.ColorRole.Window) != before
    assert created_after.palette().color(QPalette.ColorRole.Window) == existing.palette().color(
        QPalette.ColorRole.Window
    )
    service.close()
    existing.deleteLater()
    created_after.deleteLater()
    qapp.setPalette(original_palette)


@pytest.mark.parametrize(
    ("choice", "expected_mode"),
    [
        (PersistFailureChoice.KEEP_SESSION, ThemeMode.DARK),
        (PersistFailureChoice.RESTORE_PERSISTED, ThemeMode.LIGHT),
    ],
)
def test_write_failure_explicitly_keeps_session_or_restores_persisted(
    qapp,
    tmp_path: Path,
    choice: PersistFailureChoice,
    expected_mode: ThemeMode,
) -> None:
    original_palette = QPalette(qapp.palette())
    healthy = _preferences(tmp_path)
    healthy.save_theme_preference(ThemeMode.LIGHT, DEFAULT_THEME_ID)

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        del source, target
        raise OSError(r"C:\secret\transbridge.ini")

    failing = _preferences(tmp_path, replace_func=fail_replace)
    service, dialog = _dialog(qapp, failing, resolver=lambda _result: choice)
    dialog.set_draft(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID))

    assert dialog.apply_draft()

    assert service.preference.mode is expected_mode
    assert healthy.load().theme_mode is ThemeMode.LIGHT
    assert "C:\\secret" not in dialog.last_notice
    assert "transbridge.ini" not in dialog.last_notice
    assert dialog.disposed
    service.close()
    qapp.setPalette(original_palette)


def test_unknown_theme_is_rejected_before_apply_or_persistence(qapp, tmp_path: Path) -> None:
    original_palette = QPalette(qapp.palette())
    preferences = _preferences(tmp_path)
    service, dialog = _dialog(qapp, preferences)
    before = service.preference
    revision = service.snapshot().revision
    dialog.set_draft(ThemePreference(ThemeMode.DARK, "removed.provider"))

    assert not dialog.apply_draft()

    assert service.preference == before
    assert service.snapshot().revision == revision
    assert preferences.load().theme_id == DEFAULT_THEME_ID
    assert "不可用" in dialog.last_notice
    assert dialog.result() == 0
    dialog.reject()
    service.close()
    qapp.setPalette(original_palette)


def test_restore_default_is_only_a_draft_and_cancel_releases_preview(qapp, tmp_path: Path) -> None:
    original_palette = QPalette(qapp.palette())
    preferences = _preferences(tmp_path)
    preferences.save_theme_preference(ThemeMode.DARK, DEFAULT_THEME_ID)
    service, dialog = _dialog(qapp, preferences)
    revision = service.snapshot().revision

    dialog.restore_default()

    assert dialog.state.draft_preference == ThemePreference(ThemeMode.SYSTEM, DEFAULT_THEME_ID)
    assert dialog.state.dirty
    assert service.preference.mode is ThemeMode.DARK
    assert service.snapshot().revision == revision
    assert preferences.load().theme_mode is ThemeMode.DARK
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.preview.disposed
    service.close()
    qapp.setPalette(original_palette)


def test_service_settings_uses_an_integration_signal(qapp, tmp_path: Path) -> None:
    original_palette = QPalette(qapp.palette())
    service, dialog = _dialog(qapp, _preferences(tmp_path))
    requests: list[bool] = []
    dialog.service_settings_requested.connect(lambda: requests.append(True))

    dialog.service_settings_requested.emit()

    assert requests == [True]
    dialog.reject()
    service.close()
    qapp.setPalette(original_palette)


def test_preview_deduplicates_fingerprint_and_close_detaches_theme_listener(qapp, tmp_path: Path) -> None:
    original_palette = QPalette(qapp.palette())
    service, dialog = _dialog(qapp, _preferences(tmp_path))
    initial_apply_count = dialog.preview.apply_count

    dialog.set_draft(dialog.state.draft_preference)

    assert dialog.preview.apply_count == initial_apply_count
    dialog.reject()
    service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=False)
    qapp.processEvents()
    assert dialog.preview.disposed
    assert dialog.preview.apply_count == initial_apply_count
    service.close()
    qapp.setPalette(original_palette)


def test_settings_center_has_stable_sections_and_supports_deep_link(qapp, tmp_path: Path) -> None:
    service, dialog = _dialog(
        qapp,
        _preferences(tmp_path),
        initial_section="embedding",
        llm_config=LLMConfig(),
    )

    assert dialog.current_section is SettingsSection.EMBEDDING
    assert [dialog._section_list.item(index).data(256) for index in range(dialog._section_list.count())] == [
        section.value for section, _label in SECTION_LABELS
    ]
    dialog.select_section("paratranz")
    assert dialog.current_section is SettingsSection.PARATRANZ

    dialog.reject()
    service.close()


def test_service_draft_does_not_expose_secret_and_cancel_does_not_save(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[LLMConfig] = []
    monkeypatch.setattr(LLMConfig, "save_to_file", lambda config: saved.append(config))
    source = LLMConfig(model="before", api_key="secret-canary")
    service, dialog = _dialog(qapp, _preferences(tmp_path), llm_config=source)
    page = dialog._settings_pages[SettingsSection.AI_SERVICE]

    assert page.api_key_edit.text() == ""
    assert "secret-canary" not in page.api_key_edit.placeholderText()
    page.model_edit.setText("after")
    dialog.reject()

    assert source.model == "before"
    assert saved == []
    service.close()


def test_apply_persists_detached_ai_settings_and_validation_failure_stays_open(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[LLMConfig] = []
    monkeypatch.setattr(LLMConfig, "save_to_file", lambda config: saved.append(config))
    service, dialog = _dialog(qapp, _preferences(tmp_path), llm_config=LLMConfig(model="before"))
    page = dialog._settings_pages[SettingsSection.AI_SERVICE]
    page.model_edit.setText("after")
    page.concurrent_spin.setValue(7)

    assert dialog.apply_draft()
    assert saved and saved[0].model == "after"
    assert saved[0].max_concurrent == 7
    assert dialog.result() == QDialog.DialogCode.Accepted
    service.close()

    saved.clear()
    service, dialog = _dialog(qapp, _preferences(tmp_path / "invalid"), llm_config=LLMConfig(model="m"))
    page = dialog._settings_pages[SettingsSection.AI_SERVICE]
    page.base_url_edit.setText("not-a-url")

    assert not dialog.apply_draft()
    assert saved == []
    assert dialog.result() == 0
    assert "Base URL" in dialog.last_notice
    dialog.reject()
    service.close()


def test_environment_credentials_are_read_only_and_revision_conflicts_do_not_save(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[LLMConfig] = []
    monkeypatch.setattr(LLMConfig, "save_to_file", lambda config: saved.append(config))
    source = LLMConfig(model="m")
    source.config_revision = 4
    source._environment = {"TRANSBRIDGE_LLM_API_KEY": "environment-secret"}
    service, dialog = _dialog(
        qapp,
        _preferences(tmp_path),
        llm_config=source,
        reload_llm=lambda: type("Latest", (), {"config_revision": 5})(),
    )
    page = dialog._settings_pages[SettingsSection.AI_SERVICE]

    assert not page.api_key_edit.isEnabled()
    assert page.api_key_edit.text() == ""
    assert dialog._config_draft.llm._environment is source._environment
    assert not dialog.apply_draft()
    assert saved == []
    assert "其他窗口" in dialog.last_notice
    dialog.reject()
    service.close()


def test_paratranz_page_keeps_token_masked_and_notifies_after_save(qapp, tmp_path: Path) -> None:
    class Config:
        token = "paratranz-secret-canary"
        base_url = "https://paratranz.cn/api"
        timeout = 30
        user_id = 7
        saved = 0

        def update_token(self, token: str) -> None:
            self.token = token

        def update_timeout(self, timeout: int) -> None:
            self.timeout = timeout

        def delete_token(self) -> None:
            self.token = None

        def save_to_file(self) -> None:
            self.saved += 1

    config = Config()
    notified: list[object] = []
    service, dialog = _dialog(
        qapp,
        _preferences(tmp_path),
        initial_section=SettingsSection.PARATRANZ,
        paratranz_config=config,
        on_paratranz_saved=notified.append,
    )
    page = dialog._settings_pages[SettingsSection.PARATRANZ]

    assert page.token_edit.text() == ""
    assert "paratranz-secret-canary" not in page.status_label.text()
    page.timeout_spin.setValue(45)
    assert dialog.apply_draft()
    assert config.timeout == 45
    assert config.saved == 1
    assert notified == [config]
    service.close()


def test_paratranz_failed_save_restores_runtime_secret() -> None:
    class Config:
        base_url = "https://paratranz.cn/api"
        timeout = 30
        user_id = 7
        _secret = "old-token"
        _secret_source = "memory"
        credential_capability = "available"

        def update_token(self, token: str) -> None:
            self._secret = token
            self._secret_source = "memory"

        def update_timeout(self, timeout: int) -> None:
            self.timeout = timeout

        def save_to_file(self) -> None:
            raise RuntimeError("persistence failed")

    source = Config()
    draft = SettingsConfigDraft(paratranz_config=source)
    assert draft.paratranz is not None
    draft.paratranz.replacement_token = "new-token"

    result = draft.save()

    assert not result.saved
    assert source._secret == "old-token"
    assert source._secret_source == "memory"


def test_all_llm_settings_pages_round_trip_through_one_detached_save(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[LLMConfig] = []
    monkeypatch.setattr(LLMConfig, "save_to_file", lambda config: saved.append(config))
    service, dialog = _dialog(qapp, _preferences(tmp_path), llm_config=LLMConfig(model="m"))
    embedding = dialog._settings_pages[SettingsSection.EMBEDDING]
    terminology = dialog._settings_pages[SettingsSection.TERMINOLOGY]
    defaults = dialog._settings_pages[SettingsSection.AI_DEFAULTS]
    advanced = dialog._settings_pages[SettingsSection.ADVANCED]

    embedding.mode_combo.setCurrentIndex(embedding.mode_combo.findData("api"))
    embedding.base_url_edit.setText("https://embedding.example/v1")
    embedding.model_edit.setText("embedding-model")
    embedding.top_k_spin.setValue(9)
    terminology.original_col_edit.setText("C")
    terminology.translation_col_edit.setText("D")
    terminology.max_terms_spin.setValue(77)
    defaults.bool_controls["pp_enable_polish"].setChecked(True)
    advanced.mcp_enabled.setChecked(True)
    advanced.policy_combo.setCurrentIndex(advanced.policy_combo.findData("confirm"))

    assert dialog.apply_draft()
    config = saved[0]
    assert config.embedding.mode == "api"
    assert config.embedding.model == "embedding-model"
    assert config.semantic_top_k == 9
    assert config.excel_original_col == "C"
    assert config.excel_translation_col == "D"
    assert config.max_terms_per_batch == 77
    assert config.workflow_profiles["translate"]["pp_enable_polish"] is True
    assert config.mcp_enabled is True
    assert config.mcp_write_tool_policy == "confirm"
    service.close()


def test_credential_save_failure_never_closes_or_reveals_backend_details(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_config) -> None:
        raise RuntimeError("secret-canary at C:/private/config")

    monkeypatch.setattr(LLMConfig, "save_to_file", fail)
    service, dialog = _dialog(qapp, _preferences(tmp_path), llm_config=LLMConfig(model="m"))

    assert not dialog.apply_draft()
    assert dialog.result() == 0
    assert "secret-canary" not in dialog.last_notice
    assert "C:/private" not in dialog.last_notice
    dialog.reject()
    service.close()


def test_background_connection_check_ignores_late_result_after_close(qapp) -> None:
    release = threading.Event()
    button = QPushButton("测试 AI 连接")
    status = QLabel("尚未测试")

    def operation() -> ConnectionTestResult:
        release.wait(1)
        return ConnectionTestResult("info", "成功", "不应显示的迟到结果")

    controller = SettingsConnectionController(button, status, lambda: operation, idle_text="测试 AI 连接")
    assert controller.start()
    assert not button.isEnabled()
    controller.close()
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        qapp.processEvents()
        if not settings_connection_module._ACTIVE_WORKERS:
            break
        time.sleep(0.01)

    assert not settings_connection_module._ACTIVE_WORKERS
    assert "迟到结果" not in status.text()


def test_embedding_model_manager_selection_updates_only_detached_draft(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from transbridge.infra import embedding_model_store as store_module
    from transbridge.ui.tools.ai_translator import embedding_model_dialog as model_dialog_module

    class Store:
        def installed_path(self, _model_id: str):
            return None

    class Manager:
        selected_model_id = "managed-model"
        selected_model_path = tmp_path / "managed-model"

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def exec(self) -> int:
            return 1

    monkeypatch.setattr(store_module, "EmbeddingModelStore", Store)
    monkeypatch.setattr(model_dialog_module, "EmbeddingModelManagerDialog", Manager)
    source = LLMConfig(model="m")
    service, dialog = _dialog(qapp, _preferences(tmp_path), llm_config=source)
    page = dialog._settings_pages[SettingsSection.EMBEDDING]

    page.manage_models_button.click()

    assert source.embedding.local_model_id == ""
    assert dialog._config_draft.llm.embedding.local_model_id == "managed-model"
    assert dialog._config_draft.llm.embedding.mode == "local"
    dialog.reject()
    service.close()
