from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QDialog

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.workbench.cards import download_card as module

_APP = QApplication.instance() or QApplication([])


class _Signal:
    def __init__(self) -> None:
        self.values = []

    def emit(self, value) -> None:
        self.values.append(value)


class _Commands:
    def __init__(self, slot) -> None:
        self.slot = slot
        self.calls = []

    def replace_entry_records(self, patches, context, **expected):
        assert next(iter(self.slot.collection)).translation == ""
        self.calls.append((dict(patches), context, expected))
        return SimpleNamespace(is_success=True, diagnostics=())


class _Dialog:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def exec(self):
        return QDialog.DialogCode.Accepted


class _ResultDialog(_Dialog):
    pass


class _RemoteFiles:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def list_files(self, _project_id):
        return [{"id": 7, "name": "Plugin.json"}]


class _Downloader:
    def __init__(self, _config) -> None:
        pass

    def download_to_collection(self, _project_id, collection, *, file_ids):
        assert file_ids == [7]
        entry = next(iter(collection))
        collection.add(replace(entry, translation="远端译文", stage=3), overwrite=True)
        return SimpleNamespace(merged=1)


def test_authoritative_batch_download_commits_detached_candidates_once(monkeypatch) -> None:
    entry = TranslationEntry(
        "entry",
        "entry",
        "Original",
        "",
        0,
        "INFO:NAM1",
        entry_key=EntryKey(SourceNamespace("source:plugin"), "entry"),
    )
    slot = CollectionSlot("Plugin", TranslationEntryCollection((entry,)), esp_path="D:/Plugin.esp")
    commands = _Commands(slot)
    context = SimpleNamespace(
        slots={"plugin": slot},
        collection=slot.collection,
        collection_changed=_Signal(),
        config=SimpleNamespace(token="token"),
        uses_authoritative_projection=True,
        active_version_identity=("project", "variant"),
        project_revision=4,
        variant_revision=8,
        project_commands=commands,
        runtime_context=object(),
    )

    def run_worker(*_args, **kwargs):
        result = kwargs["fn_factory"](None)
        kwargs["on_result"](result)

    monkeypatch.setattr(module, "_BatchConfirmDialog", _Dialog)
    monkeypatch.setattr(module, "_BatchResultDialog", _ResultDialog)
    monkeypatch.setattr(module, "ParaTranzDownloader", _Downloader)
    monkeypatch.setattr("transbridge.paratranz.api.paratranz_files_api.ParatranzFilesAPI", _RemoteFiles)
    card = module.DownloadCard(context, run_worker)
    try:
        card.do_batch_download([slot], {"id": 1, "name": "Remote"})
        assert len(commands.calls) == 1
        assert next(iter(slot.collection)).translation == "远端译文"
        assert next(iter(slot.collection)).identity == entry.identity
        expected = commands.calls[0][2]
        assert expected["expected_project_revision"] == 4
        assert expected["expected_variant_revision"] == 8
    finally:
        card.close()
        card.deleteLater()
