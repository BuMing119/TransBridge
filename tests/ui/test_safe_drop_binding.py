from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QTableWidget, QTextEdit, QVBoxLayout, QWidget
import pytest

from transbridge.ui.drop_binding import SafeDropBinding
from transbridge.ui.drop_router import DropResolutionStatus, DropRouter
from transbridge.ui.shell.action_catalog import IntentId

_APP = QApplication.instance() or QApplication([])


def test_drop_binding_never_emits_intent_before_explicit_confirmation(tmp_path: Path) -> None:
    source = tmp_path / "Demo.esp"
    source.write_bytes(b"TES4" + b"\0" * 12)
    target = QWidget()
    binding = SafeDropBinding(target, router=DropRouter())
    resolutions = []
    confirmed = []
    binding.resolution_ready.connect(resolutions.append)
    binding.intent_confirmed.connect(lambda intent, payload: confirmed.append((intent, dict(payload))))

    resolution = binding.inspect_paths((str(source),))

    assert resolution.status is DropResolutionStatus.CANDIDATE
    assert resolutions == [resolution]
    assert confirmed == []

    assert binding.confirm(resolution)
    assert confirmed == [
        (
            IntentId.PROJECT_CREATE,
            {
                "path": str(source.resolve()),
                "drop_kind": "plugin",
                "format_id": "plugin.sse",
            },
        )
    ]
    binding.close()


def test_dismiss_and_close_do_not_dispatch_and_close_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "Demo.esp"
    source.write_bytes(b"TES4" + b"\0" * 12)
    target = QWidget()
    binding = SafeDropBinding(target, router=DropRouter())
    confirmed = []
    dismissed = []
    binding.intent_confirmed.connect(lambda *args: confirmed.append(args))
    binding.dismissed.connect(lambda: dismissed.append(True))
    resolution = binding.inspect_paths((str(source),))

    cancelled = binding.dismiss()
    binding.close()
    binding.close()

    assert cancelled.status is DropResolutionStatus.CANCELLED
    assert dismissed == [True]
    assert confirmed == []
    assert not binding.confirm(resolution)


@pytest.mark.parametrize("surface", ["window", "label", "table", "line-edit", "text-edit"])
def test_file_drag_remains_accepted_over_window_children_and_can_repeat(tmp_path, surface):
    source = tmp_path / "Demo.esp"
    source.write_bytes(b"TES4" + b"\0" * 12)
    target = QWidget()
    layout = QVBoxLayout(target)
    binding = SafeDropBinding(target)
    resolutions = []
    confirmed = []
    binding.resolution_ready.connect(resolutions.append)
    binding.intent_confirmed.connect(lambda *args: confirmed.append(args))
    # Editors may be created after the binding, including inline editors in tables.
    controls = {
        "label": QLabel("Drop here"),
        "table": QTableWidget(1, 1),
        "line-edit": QLineEdit(),
        "text-edit": QTextEdit(),
    }
    for control in controls.values():
        layout.addWidget(control)
    receiver = target if surface == "window" else controls[surface]
    if hasattr(receiver, "viewport"):
        receiver = receiver.viewport()
    target.show()
    _APP.processEvents()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    try:
        for count in (1, 2):
            args = (Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            enter = QDragEnterEvent(QPoint(5, 5), *args)
            move = QDragMoveEvent(QPoint(5, 5), *args)
            drop = QDropEvent(QPointF(5, 5), *args)
            _APP.sendEvent(receiver, enter)
            assert enter.isAccepted()
            _APP.sendEvent(receiver, move)
            assert move.isAccepted()
            assert len(resolutions) == count - 1
            _APP.sendEvent(receiver, drop)
            assert drop.isAccepted()
            assert len(resolutions) == count
            assert resolutions[-1].status is DropResolutionStatus.CANDIDATE
            assert resolutions[-1].candidate.intent_id is IntentId.PROJECT_CREATE
            assert resolutions[-1].candidate.payload_mapping()["path"] == str(source.resolve())
        assert confirmed == []
        assert controls["line-edit"].text() == ""
        assert controls["text-edit"].toPlainText() == ""
    finally:
        binding.close()
        target.close()


@pytest.mark.parametrize("owned_dialog", [False, True])
def test_file_drop_in_another_window_is_not_routed_to_the_bound_window(tmp_path, owned_dialog):
    target = QWidget()
    binding = SafeDropBinding(target)
    other = QDialog(target) if owned_dialog else QWidget()
    editor = QTextEdit(other)
    layout = QVBoxLayout(other)
    layout.addWidget(editor)
    resolutions = []
    binding.resolution_ready.connect(resolutions.append)
    target.show()
    other.show()
    _APP.processEvents()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "Demo.esp"))])
    try:
        args = (Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        _APP.sendEvent(editor.viewport(), QDragEnterEvent(QPoint(5, 5), *args))
        _APP.sendEvent(editor.viewport(), QDragMoveEvent(QPoint(5, 5), *args))
        _APP.sendEvent(editor.viewport(), QDropEvent(QPointF(5, 5), *args))
        assert resolutions == []
    finally:
        binding.close()
        other.close()
        target.close()


def test_text_drag_in_a_child_editor_keeps_its_normal_editing_behavior():
    target = QWidget()
    editor = QTextEdit(target)
    layout = QVBoxLayout(target)
    layout.addWidget(editor)
    binding = SafeDropBinding(target)
    resolutions = []
    binding.resolution_ready.connect(resolutions.append)
    target.show()
    _APP.processEvents()
    mime = QMimeData()
    mime.setText("Translation text")
    try:
        args = (Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        _APP.sendEvent(editor.viewport(), QDragEnterEvent(QPoint(5, 5), *args))
        _APP.sendEvent(editor.viewport(), QDragMoveEvent(QPoint(5, 5), *args))
        _APP.sendEvent(editor.viewport(), QDropEvent(QPointF(5, 5), *args))
        assert editor.toPlainText() == "Translation text"
        assert resolutions == []
    finally:
        binding.close()
        target.close()
