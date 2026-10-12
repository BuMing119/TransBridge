"""Exercise actual downloads through the legacy card and V2 batch publication boundary."""

from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QDialog
import pytest

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.workbench.cards import download_card as module

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def downloads(monkeypatch):
    state = SimpleNamespace(files=[], responses={}, commits=[], results=[], dialogs=[], warnings=[], information=[])
    entry = TranslationEntry(
        "entry", "entry", "Original", "", 0, "INFO:NAM1", entry_key=EntryKey(SourceNamespace("source:plugin"), "entry")
    )
    slot = CollectionSlot("Plugin", TranslationEntryCollection((entry,)), esp_path="D:/Plugin.esp")

    def commit(patches, _context, **_expected):
        assert next(iter(slot.collection)).translation == ""
        state.commits.append(dict(patches))
        return SimpleNamespace(is_success=True, diagnostics=())

    context = SimpleNamespace(
        slots={"plugin": slot},
        collection=slot.collection,
        collection_changed=SimpleNamespace(emit=lambda _collection: None),
        config=SimpleNamespace(token="token"),
        uses_authoritative_projection=True,
        active_version_identity=("project", "variant"),
        project_revision=4,
        variant_revision=8,
        project_commands=SimpleNamespace(replace_entry_records=commit),
        runtime_context=object(),
    )

    class RemoteFiles:
        def __init__(self, *_args, **_kwargs):
            pass

        def list_files(self, _project_id):
            return state.files

        def get_file_translation(self, _project_id, file_id):
            response = state.responses[file_id]
            if isinstance(response, Exception):
                raise response
            return response

    class Dialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_file_ids(self):
            return [file["id"] for file in state.files]

    class ResultDialog(Dialog):
        def __init__(self, title, header, items, **_kwargs):
            state.dialogs.append((title, header, items))

    def run_worker(**kwargs):
        result = kwargs["fn_factory"](None)
        state.results.append(result)
        kwargs["on_result"](result)

    monkeypatch.setattr("transbridge.paratranz.workflow.downloader.ParatranzFilesAPI", RemoteFiles)
    monkeypatch.setattr("transbridge.paratranz.api.paratranz_files_api.ParatranzFilesAPI", RemoteFiles)
    monkeypatch.setattr("transbridge.ui.paratranz.target_context.bound_paratranz_project", lambda _ctx: {"id": 1})
    monkeypatch.setattr(module, "_BatchConfirmDialog", Dialog)
    monkeypatch.setattr(module, "_FileSelectDialog", Dialog)
    monkeypatch.setattr(module, "_BatchResultDialog", ResultDialog)
    monkeypatch.setattr(module.QMessageBox, "warning", lambda _parent, *args: state.warnings.append(args))
    monkeypatch.setattr(module.QMessageBox, "information", lambda _parent, *args: state.information.append(args))
    card = module.DownloadCard(context, run_worker)

    def run(*, legacy=False):
        context.uses_authoritative_projection = not legacy
        if legacy:
            card.download()
        else:
            card.do_batch_download([slot], {"id": 1, "name": "Remote"})
        return state.results[-1]

    yield run, state, slot
    card.close()
    card.deleteLater()


def test_batch_download_all_files_fail_without_success_or_empty_commit(downloads):
    run, state, slot = downloads
    state.files = [{"id": 7, "name": "Plugin.json"}]
    state.responses = {7: RuntimeError("controlled download timeout")}

    result = run()

    assert (result.success_count, result.partial_count, result.failed_count) == (0, 0, 1)
    assert result.merged_total == 0
    assert result.updated_collections == state.commits == []
    assert next(iter(slot.collection)).translation == ""
    assert "Plugin.json: controlled download timeout" in result.details[0]
    assert "成功：0 个" in state.dialogs[0][1]
    assert "失败：1 个" in state.dialogs[0][1]


def test_batch_download_partial_failure_preserves_and_commits_successful_file(downloads):
    run, state, slot = downloads
    state.files = [{"id": 7, "name": "Plugin.json"}, {"id": 8, "name": "Plugin_1.json"}]
    state.responses = {
        7: [{"key": "entry", "translation": "Translated", "stage": 3}],
        8: RuntimeError("controlled download timeout"),
    }

    result = run()

    assert (result.success_count, result.partial_count, result.failed_count) == (0, 1, 0)
    assert result.merged_total == 1
    assert len(state.commits) == 1
    assert next(iter(state.commits[0].values())).translation == "Translated"
    assert next(iter(slot.collection)).translation == "Translated"
    assert "Plugin_1.json: controlled download timeout" in result.details[0]
    assert "部分完成：1 个" in state.dialogs[0][1]


@pytest.mark.parametrize("response", [[], [{"key": "unknown", "translation": "Other", "stage": 3}]])
def test_batch_download_valid_no_merge_does_not_report_failure_or_commit(downloads, response):
    run, state, slot = downloads
    state.files = [{"id": 7, "name": "Plugin.json"}]
    state.responses = {7: response}

    result = run()

    assert (result.success_count, result.partial_count, result.failed_count) == (1, 0, 0)
    assert result.merged_total == 0
    assert state.commits == []
    assert next(iter(slot.collection)).translation == ""


def test_batch_download_without_same_name_file_is_skipped(downloads):
    run, state, _slot = downloads
    state.files = [{"id": 7, "name": "Other.json"}]

    result = run()

    assert (result.success_count, result.skipped_count, result.failed_count) == (0, 1, 0)
    assert "未找到同名文件" in result.details[0]
    assert state.commits == []


@pytest.mark.parametrize("partial", [False, True])
def test_legacy_single_download_shows_file_failures_and_keeps_successes(downloads, partial):
    run, state, slot = downloads
    state.files = [{"id": 7, "name": "Plugin.json"}]
    state.responses = {7: RuntimeError("controlled download timeout")}
    if partial:
        state.files.append({"id": 8, "name": "Plugin_1.json"})
        state.responses[8] = [{"key": "entry", "translation": "Translated", "stage": 3}]

    result = run(legacy=True)

    assert len(result.failed_files) == 1
    assert result.merged == int(partial)
    assert next(iter(slot.collection)).translation == ("Translated" if partial else "")
    assert state.information == []
    title, message = state.warnings[0]
    assert title == ("下载部分完成" if partial else "下载失败")
    assert "Plugin.json: controlled download timeout" in message


def test_legacy_single_download_empty_remote_file_is_successful(downloads):
    run, state, _slot = downloads
    state.files = [{"id": 7, "name": "Plugin.json"}]
    state.responses = {7: []}

    run(legacy=True)

    assert state.warnings == []
    assert state.information[0][0] == "下载合并完成"
