"""Only current source paths and their 413 partitions are download candidates."""

from types import SimpleNamespace

import pytest

from transbridge.application.categorized_upload import UploadSource
from transbridge.application.file_upload import upload_files
from transbridge.application.io.identity import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.sync import SyncOperation
from transbridge.application.sync.models import LocalEntrySnapshot
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.paratranz.path_snapshot import current_category_paths, matches_current_path
from transbridge.ui.operations.categorized_download_scope import download_snapshot_adapter
from transbridge.ui.operations.ordinary_upload_scope import ordinary_upload_path

NS = SourceNamespace("source:b")


def _entry(key, original="Original", context="NPC_:FULL"):
    return TranslationEntry(key, key, original, "", 0, context, entry_key=EntryKey(NS, key))


def _entries(title="Quest Name"):
    return (
        _entry("Quest:00000010|1~QUST:FULL", title, "QUST:FULL"),
        _entry("line", context="INFO:NAM1|00000010"),
        _entry("npc"),
    )


def _forbidden(*args, **kwargs):
    raise AssertionError("historical bindings and project-wide strings must not be consulted")


def _request(entries=None, *, collection=True):
    entries = _entries() if entries is None else entries
    context = SimpleNamespace(
        config=object(),
        collection=entries if collection else None,
        project_sources=({"source_id": "b", "location": "B.esp", "display_name": "B.esp", "namespace": NS.value},),
        project_relations=(),
        project_commands=SimpleNamespace(
            categorized_upload_file_scope=_forbidden, categorized_upload_bindings=_forbidden
        ),
        slots={},
    )
    return SimpleNamespace(
        ui_context=context,
        operation=SyncOperation.DOWNLOAD,
        namespace=NS,
        local_entries=tuple(
            LocalEntrySnapshot(e.identity, EntryRevision(), e.original, e.translation, e.context or "", 0)
            for e in entries
        ),
    )


class Gateway:
    def __init__(self, files):
        self.files = files
        self.reads = []
        self.closed = False

    def inventory(self, project_id):
        return self.files

    def get_file_translation(self, project_id, file_id):
        self.reads.append(file_id)
        return [{"id": file_id * 10, "key": "line", "original": "Original", "translation": str(file_id)}]

    def close(self):
        self.closed = True


def _fetch(request, files):
    adapter = download_snapshot_adapter(SimpleNamespace(list_entries=_forbidden, get_entry=_forbidden), request)
    gateway = Gateway(files)
    adapter.gateway_factory = lambda _config: gateway
    entries = adapter.fetch(7, NS, limit=100)
    assert gateway.closed
    return entries, gateway


def test_unbound_source_reads_only_current_paths_and_numeric_parts():
    files = {
        "B.esp.json": 1,
        "B.esp_2.json": 2,
        "B.esp/人名.json": 3,
        "B.esp/任务/对话_[Quest Name].json": 4,
        "B.esp/任务/对话_[Quest Name]_3.json": 5,
        "B.esp/任务/任务日志.json": 6,
        "B.esp/任务/对话_[00000010].json": 7,
        "B.esp/任务/对话_[Old Name].json": 8,
        "B.esp/任务/对话_[Quest Name]__冲突_old.json": 9,
        "B.esp/其他.json": 10,
        "A.esp/人名.json": 11,
        "Other/B.esp.json": 12,
    }

    entries, gateway = _fetch(_request(), files)

    assert gateway.reads == [1, 2, 3, 4, 5, 6]
    assert [entry.translation for entry in entries] == [str(i) for i in range(1, 7)]


def test_only_old_layout_available_yields_no_candidates_without_fallback():
    entries, gateway = _fetch(_request(), {"B.esp/任务/对话_[00000010].json": 99})
    assert entries == ()
    assert gateway.reads == []


def test_scope_uses_complete_collection_even_when_request_selection_omits_quest_title():
    request = _request()
    request.local_entries = request.local_entries[1:2]
    adapter = download_snapshot_adapter(object(), request)
    assert "B.esp/任务/对话_[Quest Name].json" in adapter.paths
    assert "B.esp/任务/对话_[00000010].json" not in adapter.paths


def test_headless_complete_snapshots_reconstruct_quest_title_without_saved_ids():
    request = _request(collection=False)
    adapter = download_snapshot_adapter(object(), request)
    assert "B.esp/任务/对话_[Quest Name].json" in adapter.paths


def test_current_title_renaming_excludes_old_path_and_uses_current_inventory_id():
    entries, gateway = _fetch(
        _request(_entries("New Name")),
        {"B.esp/任务/对话_[Quest Name].json": 10, "B.esp/任务/对话_[New Name].json": 99},
    )
    assert gateway.reads == [99]
    assert entries[0].translation == "99"


def test_unknown_source_root_errors_instead_of_falling_back_to_project_strings():
    request = _request()
    request.ui_context.project_sources = ()
    with pytest.raises(ValueError, match="路径"):
        download_snapshot_adapter(object(), request)


def test_non_download_operations_do_not_get_an_unscoped_download_adapter():
    request = _request()
    request.operation = SyncOperation.UPLOAD
    with pytest.raises(ValueError, match="下载"):
        download_snapshot_adapter(object(), request)


def test_current_scope_matches_upload_paths_with_source_label_collision():
    entries = _entries()
    sources = (
        UploadSource("a", "B.esp", ()),
        UploadSource("b", "B.esp", entries),
    )
    request = _request()
    request.ui_context.project_sources = (
        {"source_id": "a", "location": "A.esp", "display_name": "B.esp", "namespace": "source:a"},
        {"source_id": "b", "location": "B.esp", "display_name": "B.esp", "namespace": NS.value},
    )
    root = ordinary_upload_path(request.ui_context, entries=entries)
    paths = current_category_paths(root, entries)
    assert set(paths) == {file.path for file in upload_files(sources, categorized=True)}
    assert root == next(file.path for file in upload_files(sources, categorized=False))


@pytest.mark.parametrize("path", ["B.esp_1.json", "B.esp_123.json", "B.esp/人名_2.json"])
def test_numeric_413_parts_match_only_a_current_base(path):
    assert matches_current_path(path, {"B.esp.json", "B.esp/人名.json"})


@pytest.mark.parametrize(
    "path",
    [
        "B.esp_0.json",
        "B.esp_1_2.json",
        "B.esp_01.json",
        "B.esp_1.json.bak",
        "X/B.esp_1.json",
        "B.esp/人名__冲突_a.json",
    ],
)
def test_historical_or_unrelated_files_do_not_match_numeric_parts(path):
    assert not matches_current_path(path, {"B.esp.json", "B.esp/人名.json"})


def test_source_collection_without_request_namespace_is_rejected():
    request = _request()
    request.namespace = SourceNamespace("source:missing")
    with pytest.raises(ValueError, match="插件来源"):
        download_snapshot_adapter(object(), request)


def test_captured_root_path_is_used_for_download_scope():
    request = _request()
    request.upload_path = "Captured.esp.json"
    adapter = download_snapshot_adapter(object(), request)
    assert adapter.root_path == "Captured.esp.json"
    assert "Captured.esp/任务/对话_[Quest Name].json" in adapter.paths
