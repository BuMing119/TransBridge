"""Run real Qt scaling in separate processes; each process keeps its windows through theme changes."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("scale", (1.25, 1.5, 2.0))
def test_management_keyboard_and_dialogs_at_real_dpi(tmp_path, scale):
    result = subprocess.run(
        [sys.executable, "-m", __name__, str(scale), str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "QT_SCALE_FACTOR": str(scale), "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["scale"] == scale
    assert evidence["captures"] == 21


def _probe(scale, output):
    # Imports happen after the child process has its scale factor, before QApplication is constructed.
    from PyQt6.QtCore import QCoreApplication, QEvent, Qt, QTimer
    from PyQt6.QtGui import QFont, QFontDatabase
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QComboBox, QLineEdit, QPushButton, QTextEdit

    from tests.ui.responsive_layout_support import apply_layout_font, settle_layout
    from tests.ui.test_paratranz_management_context import _DATA, _PAGES, _Context, _Worker
    from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
    from transbridge.ui.foundation.builtins import create_builtin_registry
    from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService
    from transbridge.ui.paratranz.overview_tab import EditProjectDialog
    from transbridge.ui.paratranz.terms_tab import TermFormDialog

    app = QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    app.setFont(QFont("Microsoft YaHei", 10))
    theme = ThemeService(
        app,
        create_builtin_registry(),
        SimpleNamespace(
            load=lambda: SimpleNamespace(theme_mode=ThemeMode.LIGHT, theme_id=DEFAULT_THEME_ID, diagnostics=())
        ),
    )
    theme.start()
    output.mkdir(parents=True, exist_ok=True)
    evidence = {"scale": scale, "captures": 0, "windows": []}

    def capture(widget, name):
        settle_layout(app)
        assert widget.devicePixelRatioF() == pytest.approx(scale)
        buttons = [button for button in widget.findChildren(QPushButton) if button.isVisible()]
        for button in buttons:
            assert button.font().pointSizeF() == 14
            assert button.width() >= button.minimumSizeHint().width(), (name, button.text(), button.size())
            assert widget.rect().contains(button.rect().translated(button.mapTo(widget, button.rect().topLeft())))
        for control in widget.findChildren((QLineEdit, QComboBox, QTextEdit)):
            if control.isVisible():
                assert widget.rect().contains(
                    control.rect().translated(control.mapTo(widget, control.rect().topLeft()))
                )
        pixmap = widget.grab()
        assert pixmap.width() == round(widget.width() * scale)
        assert pixmap.height() == round(widget.height() * scale)
        assert pixmap.save(str(output / f"{name}.png"))
        evidence["captures"] += 1
        evidence["windows"].append({"name": name, "logical": [widget.width(), widget.height()], "dpr": scale})

    def themes(widget, name, size):
        widget.show()
        for label, mode in (("light", ThemeMode.LIGHT), ("dark", ThemeMode.DARK), ("return", ThemeMode.LIGHT)):
            theme.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
            apply_layout_font(widget, theme.snapshot(), 14)
            widget.resize(*size)
            widget.activateWindow()
            capture(widget, f"{name}-{label}")
            yield label

    def dispose(widget):
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    with pytest.MonkeyPatch.context() as patch:
        for kind, (module, cls) in _PAGES.items():
            patch.setattr(module, "ApiWorker", _Worker)
            context = _Context()
            page = cls(context)
            context.current_project = {"id": 7, "name": "Synthetic project / 合成测试项目"}
            page._workers[-1].result.emit(_DATA[kind])
            if hasattr(page, "_table"):
                page._table.selectRow(0)
            for _label in themes(page, kind, (760, 560)):
                assert page.size().width() == 760
                first = page._edit_btn if kind == "overview" else page._refresh_btn
                second = (
                    page._del_btn if kind == "overview" else (page._upload_btn if kind == "files" else page._add_btn)
                )
                first.setFocus()
                QTest.keyClick(first, Qt.Key.Key_Tab)
                assert app.focusWidget() is second, (kind, _label, app.focusWidget(), second)
                QTest.keyClick(second, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
                assert app.focusWidget() is first
                danger = {
                    "files": "_delete_btn",
                    "members": "_remove_btn",
                    "terms": "_del_btn",
                    "overview": "_del_btn",
                }[kind]
                for key in (Qt.Key.Key_Return, Qt.Key.Key_Escape):
                    before = len(page._workers)
                    QTimer.singleShot(0, lambda key=key: QTest.keyClick(app.activeModalWidget(), key))
                    button = getattr(page, danger)
                    button.setFocus()
                    QTest.keyClick(button, Qt.Key.Key_Space)
                    assert len(page._workers) == before
            if kind == "members":
                failures = []
                before = len(page._workers)

                def member_keys():
                    dialog = app.activeModalWidget()
                    try:
                        editor = dialog.findChild(QLineEdit)
                        combo = dialog.findChild(QComboBox)
                        editor.setText("42")
                        combo.setCurrentIndex(1)
                        for _label in themes(dialog, "member-form", (420, 240)):
                            editor.setFocus()
                            QTest.keyClick(editor, Qt.Key.Key_Tab)
                            assert app.focusWidget() is combo
                            QTest.keyClick(combo, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
                            assert app.focusWidget() is editor
                            assert editor.text() == "42"
                            assert combo.currentIndex() == 1
                    except BaseException as error:
                        failures.append(error)
                    finally:
                        QTest.keyClick(dialog, Qt.Key.Key_Escape)

                QTimer.singleShot(0, member_keys)
                QTest.mouseClick(page._add_btn, Qt.MouseButton.LeftButton)
                assert not failures, failures
                assert len(page._workers) == before
            dispose(page)

        for kind, dialog in (
            ("term-form", TermFormDialog({"term": "Dragon", "translation": "合成译文", "note": "draft"})),
            ("project-form", EditProjectDialog({"id": 7, "name": "合成项目", "desc": "draft"})),
        ):
            first = dialog.findChild(QLineEdit)
            for _label in themes(dialog, kind, (420, 560)):
                first.setFocus()
                QTest.keyClick(first, Qt.Key.Key_Tab)
                following = app.focusWidget()
                assert following is not first
                QTest.keyClick(following, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
                assert app.focusWidget() is first
                assert "draft" in str(dialog.get_data())
            first.setFocus()
            QTest.keyClick(first, Qt.Key.Key_Return)
            assert dialog.result() == dialog.DialogCode.Accepted
            dispose(dialog)
    theme.close()
    (output / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    _probe(float(sys.argv[1]), Path(sys.argv[2]))
