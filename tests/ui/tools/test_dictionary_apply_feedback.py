from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from transbridge.ui.tools import dictionary_panel

_APP = QApplication.instance() or QApplication([])


def test_unavailable_save_restores_entries_and_keeps_technical_reason_in_details(monkeypatch):
    entry = SimpleNamespace(identity="one", translation="原译文", stage=1)

    def apply(collection, **_kwargs):
        collection[0].translation = "词典译文"
        collection[0].stage = 5
        return SimpleNamespace(key_hits=1, text_hits=0, applied=1, misses=0, needs_review=(), conflicts=())

    manager = SimpleNamespace(load=lambda: None, apply_to_collection=apply)
    monkeypatch.setattr(dictionary_panel, "TranslationMemoryManager", lambda: manager)
    messages = []

    def show_error(dialog):
        messages.append((dialog.text(), dialog.detailedText()))
        assert (entry.translation, entry.stage) == ("原译文", 1)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "exec", show_error)
    panel = dictionary_panel.DictionaryPanel.__new__(dictionary_panel.DictionaryPanel)
    QDialog.__init__(panel)
    panel._ctx = SimpleNamespace(collection=[entry], uses_authoritative_projection=True)
    try:
        panel._on_apply_dict()
        assert messages == [("工程写入服务不可用，词典尚未套用。", "权威 Variant 写入适配器不可用。")]
    finally:
        panel.close()
