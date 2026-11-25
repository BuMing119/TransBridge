from dataclasses import replace
from threading import current_thread, main_thread
from types import SimpleNamespace

from PyQt6.QtCore import QEvent, QTimer
from PyQt6.QtGui import QHelpEvent
from PyQt6.QtWidgets import QApplication, QComboBox, QMessageBox, QToolTip

from tests.paratranz.test_project_upload_policy import MemoryProject
from transbridge.application.file_upload_modes import UploadOperation
from transbridge.application.upload_policy import ProjectRole, TranslationMode, UploadLayout
from transbridge.paratranz.project_policy import PolicyAccess, default_upload_policy, publish_policy
from transbridge.ui.operations.upload_mode_dialog import apply_operation_access
from transbridge.ui.operations.upload_policy_editor import UploadPolicyEditor
from transbridge.ui.operations.upload_policy_flow import edit_policy, initial_policy


def test_owner_default_and_custom_editor_publish_only_cloud_file(tmp_path, monkeypatch):
    qapp = QApplication.instance() or QApplication([])
    monkeypatch.chdir(tmp_path)
    cloud = MemoryProject()
    threads = []

    def task(cancel, *, previous=None, policy=None):
        threads.append(current_thread())
        return cloud.window() if policy is None else publish_policy(cloud, 42, previous, policy, lambda: None)

    session = SimpleNamespace(policy_access=task, check_policy_target=lambda: None)
    prompts = []
    saved_notices = []
    timer = QTimer()

    def choose():
        dialog = qapp.activeModalWidget()
        if isinstance(dialog, QMessageBox):
            if dialog.text() == "要为这个项目设置上传规则吗？":
                prompts.append(dialog.text())
                next(button for button in dialog.buttons() if "默认权限" in button.text()).click()
            elif dialog.windowTitle() == "上传规则已保存":
                saved_notices.append((dialog.text(), cloud.window().policy))
                next(button for button in dialog.buttons() if button.text() == "继续").click()
            else:
                dialog.reject()

    timer.timeout.connect(choose)
    timer.start(10)
    try:
        first = initial_policy(session, None)
        next_open = initial_policy(session, None)
    finally:
        timer.stop()
    assert len(prompts) == 1 and first == next_open and cloud.writes == ["create"]
    assert saved_notices == [("默认上传规则已保存到当前 ParaTranz 项目。", first.policy)]
    assert all(thread is not main_thread() for thread in threads)
    editor = UploadPolicyEditor(first)
    editor.layout_choice.setCurrentIndex(editor.layout_choice.findData(UploadLayout.CATEGORIZED.value))
    candidate = editor.policy()
    assert candidate.layout == UploadLayout.CATEGORIZED
    assert candidate.permits_operation(UploadOperation.FILL_EMPTY, user_id=102, role=ProjectRole.ADMIN)
    editor.reject()
    assert cloud.window() == first and not list(tmp_path.iterdir())

    def select_layout_and_save():
        active = qapp.activeModalWidget()
        if isinstance(active, UploadPolicyEditor):
            active.layout_choice.setCurrentIndex(active.layout_choice.findData("categorized"))
            active.accept()

    QTimer.singleShot(0, select_layout_and_save)
    saved = edit_policy(session, first, None)
    assert saved.policy == candidate and cloud.window() == saved
    assert cloud.writes == ["create", "replace"]
    assert all(thread is not main_thread() for thread in threads) and not list(tmp_path.iterdir())


def test_combo_keeps_denied_modes_visible_and_selects_first_available():
    app = QApplication.instance() or QApplication([])
    combo = QComboBox()
    for mode in UploadOperation:
        combo.addItem(mode.name, mode.value)
    default = default_upload_policy()
    policy = replace(
        default,
        translation_modes=frozenset({TranslationMode.FILL_EMPTY}),
        rules={UploadOperation.FILL_EMPTY: default.rules[UploadOperation.FILL_EMPTY]},
    )
    access = PolicyAccess(1, policy, 101, ProjectRole.ADMIN, ())
    apply_operation_access(combo, access)
    assert combo.currentData() == "trans_fill_empty" and combo.count() == 5
    assert not combo.model().item(0).isEnabled()
    combo.show()
    combo.showPopup()
    app.processEvents()
    view = combo.view()
    viewport = view.viewport()
    for mode, reason in (
        (UploadOperation.FORCE, "项目未开放「强制导入译文」。"),
        (UploadOperation.ORIGINAL, "当前账号未获此操作的授权。"),
    ):
        index = combo.findData(mode.value)
        pos = view.visualRect(combo.model().index(index, 0)).center()
        app.sendEvent(viewport, QHelpEvent(QEvent.Type.ToolTip, pos, viewport.mapToGlobal(pos)))
        assert QToolTip.text() == reason
        assert combo.itemText(index) == mode.name and not combo.model().item(index).isEnabled()
    combo.hidePopup()
    apply_operation_access(combo, replace(access, policy=replace(policy, rules={})))
    assert combo.currentIndex() == -1
    apply_operation_access(combo, replace(access, policy=None))
    assert all(not combo.model().item(i).toolTip() for i in range(combo.count()))
    combo.close()
    app.processEvents()
