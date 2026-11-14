from types import SimpleNamespace

from PyQt6.QtWidgets import QComboBox, QDialog, QFileDialog, QLabel, QPushButton, QWidget
import pytest

from tests.ui.test_ui_settings_dialog import _dialog, _preferences, qapp as qapp_fixture
from transbridge.application.contracts import OperationResult, RequestContext
from transbridge.config.llm import LLMConfig
from transbridge.config.text_encoding import TextEncodingDefaults, TextEncodings
from transbridge.ui.coordinators import project_text_encoding_coordinator as module
from transbridge.ui.project_text_encoding_dialog import ProjectTextEncodingDialog
from transbridge.ui.settings.sections import SettingsSection

qapp = qapp_fixture


class _EncodingService:
    def __init__(self, events):
        self.events = events

    def active_encodings(self, _request):
        return TextEncodings()

    def active_project_path(self, _request):
        return "project.sqlite3"

    def prepare(self, path, encodings, request, *, cancelled):
        self.events.append(("prepare", path, encodings, request.project_id))
        assert not cancelled()
        return OperationResult.completed(SimpleNamespace(token="plan", sources=(), encodings=encodings))

    def commit(self, token, request, *, cancelled):
        assert not cancelled()
        self.events.append(("commit", token, request.project_id))
        return OperationResult.completed(SimpleNamespace(project_path="project.sqlite3", backup_path=None))

    def discard(self, token, request):
        self.events.append(("discard", token, request.project_id))


def _host(_qapp, coordinator_service, events):
    host = QWidget()
    host.context = SimpleNamespace(
        active_project_id="project",
        active_variant_id="variant",
        project_opening=False,
        project_view_failed=False,
        project_text_encodings=coordinator_service,
    )
    host.runtime_context = RequestContext("gui")
    host.show_message = lambda message, **_kwargs: events.append(("message", message))
    host.project_coordinator = SimpleNamespace(open_project_path=lambda path: events.append(("open", path)))

    def dispatch(operation, *, on_result, **_kwargs):
        on_result(operation())
        return True

    host.start_foreground_task = dispatch
    return host


@pytest.mark.parametrize(
    "selected",
    [
        TextEncodings("cp1252", "cp936"),
        TextEncodings(source_language="russian"),
        TextEncodings("utf-8", "cp936"),
    ],
)
def test_encoding_save_never_locates_or_reloads_sources(qapp, monkeypatch, selected):
    events = []
    host = _host(qapp, _EncodingService(events), events)

    def unexpected_input(*_args):
        pytest.fail("Saving settings must not ask for source files")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", unexpected_input)
    monkeypatch.setattr(
        module,
        "ProjectTextEncodingDialog",
        lambda *_args: SimpleNamespace(
            exec=lambda: QDialog.DialogCode.Accepted,
            deleteLater=lambda: None,
            fields=SimpleNamespace(value=lambda: selected),
        ),
    )
    coordinator = module.ProjectTextEncodingCoordinator(host)
    coordinator.start()
    assert [event[0] for event in events if event[0] != "message"] == ["prepare", "commit"]
    assert ("prepare", "project.sqlite3", selected, "project") in events
    assert any("更新来源" in event[1] for event in events if event[0] == "message")
    assert coordinator.availability()[0]
    host.close()


def test_project_languages_bind_codecs_without_a_reread_action(qapp):
    settings = ProjectTextEncodingDialog(TextEncodings("cp1252", "cp936"))
    assert not settings._next.isEnabled()
    settings.fields.source_combo.setCurrentIndex(settings.fields.source_combo.findData("russian"))
    assert settings.fields.value() == TextEncodings("utf-8", "cp936", "russian", "chinese")
    assert "1251" in settings.fields.read_notice.text()
    assert settings._next.isEnabled()
    assert settings._next.text() == "保存"
    settings.fields.target_combo.setCurrentIndex(settings.fields.target_combo.findData("french"))
    assert settings.fields.value() == TextEncodings("utf-8", "utf-8", "russian", "french")
    settings.fields.input_combo.setCurrentIndex(settings.fields.input_combo.findData("cp936"))
    assert "936" in settings.fields.read_notice.text()
    assert "1251" not in settings.fields.read_notice.text()
    assert not any(
        "重读" in button.text() or "重新读取" in button.text() for button in settings.findChildren(QPushButton)
    )
    settings.close()


@pytest.mark.parametrize("point_size", [10, 14])
def test_project_encoding_dialog_fits_language_selectors_and_footer(qapp, point_size):
    dialog = ProjectTextEncodingDialog(TextEncodings("cp936", "cp1252"))
    font = dialog.font()
    font.setPointSize(point_size)
    dialog.setFont(font)
    dialog.resize(520, 350)
    dialog.show()
    qapp.processEvents()
    assert dialog.width() == 520
    for child in (*dialog.findChildren(QPushButton), *dialog.findChildren(QLabel), *dialog.findChildren(QComboBox)):
        if child.isVisible():
            center = child.mapTo(dialog, child.rect().center())
            assert dialog.rect().contains(center)
    dialog.close()


def test_encoding_settings_apply_independently_and_cancel_discards_edits(qapp, tmp_path):
    preferences = _preferences(tmp_path)
    defaults = TextEncodingDefaults(preferences._repository)
    service, dialog = _dialog(qapp, preferences, initial_section="text_encoding", text_encoding_defaults=defaults)
    page = dialog._settings_pages[SettingsSection.TEXT_ENCODING]
    page.fields.input_combo.setCurrentIndex(page.fields.input_combo.findData("cp1252"))
    page.fields.output_combo.setCurrentIndex(page.fields.output_combo.findData("cp936"))
    assert defaults.load() == TextEncodings()
    dialog.reject()
    service.close()
    assert defaults.load() == TextEncodings()

    service, dialog = _dialog(
        qapp,
        preferences,
        initial_section="text_encoding",
        text_encoding_defaults=defaults,
        llm_config=LLMConfig(base_url="invalid-domain"),
    )
    page = dialog._settings_pages[SettingsSection.TEXT_ENCODING]
    page.fields.input_combo.setCurrentIndex(page.fields.input_combo.findData("cp1252"))
    page.fields.output_combo.setCurrentIndex(page.fields.output_combo.findData("cp936"))
    assert dialog.apply_draft()
    assert defaults.load() == TextEncodings("cp1252", "cp936")
    assert "已保存" in dialog.last_notice
    dialog.reject()
    service.close()
