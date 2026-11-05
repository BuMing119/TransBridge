import json
from types import SimpleNamespace

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QWidget
import pytest

from tests.ui.workbench.terminology_async_support import wait_profile
from transbridge.application.contracts import RequestContext
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    TerminologyProfileImportPolicy,
    TerminologyProfileService,
)
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.window import TerminologyWindow
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBar
from transbridge.ui.workbench.terminology_profile_controller import TerminologyProfileUiController

_APP = QApplication.instance() or QApplication([])


class _Context(QObject):
    project_changed = pyqtSignal()
    variant_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.active_version_identity = ("project", "variant")


class _Preview(QWidget):
    def set_terminology_profile(self, _profile, *, base_snapshot=None, source_profile=None):
        pass


@pytest.mark.parametrize("policy", tuple(TerminologyProfileImportPolicy))
def test_real_window_binding_forwards_policy_through_shared_reader_preview_and_profile_service(
    tmp_path, monkeypatch, policy
):
    service = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    base = EffectiveTerminologySnapshot(
        "project",
        "variant",
        EffectiveSnapshotStatus.READY,
        version_id="base",
        content_digest="base-digest",
        decisions=(TermDecision("a", "project", "variant", "A", "a", "已有译名", status=DecisionStatus.ADOPTED),),
    )
    factory = SimpleNamespace(
        profile_service_for=lambda _project: service, base_terminology_snapshot=lambda *_args: base
    )
    context, bar, preview_widget = _Context(), TerminologyProfileBar(), _Preview()
    profiles = TerminologyProfileUiController(context, factory, bar, preview_widget)
    wait_profile(profiles)
    presenter = TerminologyPresenter(
        TerminologyUiServices(), RequestContext("operator", project_id="project", variant_id="variant")
    )
    window = TerminologyWindow(presenter, ui_context=context, profile_controller=profiles)
    captured_previews, messages, failures = [], [], []
    source_path = tmp_path / "terms.json"
    source_path.write_text(
        json.dumps([{"term": "A", "translation": "导入译名"}, {"term": "B", "translation": "来源独有"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(source_path), ""))

    class PolicyDialog:
        def __init__(self, selection, _parent):
            assert selection.request.file_path == str(source_path)
            self.policy = policy

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        "transbridge.ui.tools.terminology.schemes_controller.TerminologyImportPolicyDialog", PolicyDialog
    )

    class ImportDialog:
        def __init__(self, chosen_preview, default_name, _parent, *, select_after_create):
            captured_previews.append(chosen_preview)
            self.profile_name, self.select_after_create = default_name, select_after_create

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr("transbridge.ui.tools.terminology_profiles.TerminologySourceImportDialog", ImportDialog)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: failures.append(_args[2]))
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: messages.append(_args[2]))
    # Run the actual reader task deterministically; do not bypass the window's
    # context wrapper or preview/create/publication application services.
    monkeypatch.setattr(
        "transbridge.ui.tools.terminology_profiles.source_import_controller.QThreadPool.globalInstance",
        lambda: SimpleNamespace(start=lambda task: task.run()),
    )
    try:
        window.schemes_view.import_button.click()
        assert failures == []
        assert len(captured_previews) == len(messages) == 1
        assert captured_previews[0].policy is policy
        wait_profile(profiles)
        assert service.selected_revision("project", "variant") is None
        assert profiles.state.applied_revision is None
        published = profiles.state.selected_revision
        assert published is not None
        mappings = {item.original: item for item in published.content.mappings}
        assert mappings["A"].translation == (
            "已有译名" if policy is TerminologyProfileImportPolicy.KEEP_EXISTING else "导入译名"
        )
        assert mappings["B"].translation == "来源独有" and mappings["B"].scheme_owned
    finally:
        wait_profile(profiles)
        window.close()
        window._actions._pool.waitForDone(3000)
        profiles._refresh.close()
        bar.close()
        preview_widget.close()
        _APP.processEvents()
