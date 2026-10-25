"""The real ordinary-upload preparation and executor honor a fixed root file."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.conftest import make_test_collection
from tests.ui.operations import test_paratranz_upload_roundtrip as roundtrip_helpers
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.operations.ordinary_upload_scope import ordinary_upload_path
from transbridge.ui.operations.plan_view import OperationKind

roundtrip_case = roundtrip_helpers.roundtrip_case


@pytest.mark.parametrize("root_exists", [False, True])
def test_production_upload_ignores_same_content_and_saved_id_in_other_paths(roundtrip_case, root_exists):
    case = roundtrip_case(size=1, qualified=True)
    source = case.sources[0]
    case.strings.entries[7] = ParaTranzEntry(7, source.key, source.original, "旧绑定译文", "旧上下文", 1)
    assert case.execute(case.check(OperationKind.DOWNLOAD)).is_success
    assert not case.context.collection.get(source.identity).external_refs
    case.edit(source, "本地译文")
    case.gateway.files = {12: "source.esp/物品.json", 13: "other/source.esp.json"}
    if root_exists:
        case.gateway.files[11] = "source.esp.json"
    case.strings.entries.update({
        7: ParaTranzEntry(7, source.key, source.original, "分类译文", "分类上下文", 1, 12),
        8: ParaTranzEntry(8, source.key, source.original, "其他目录译文", "其他上下文", 1, 13),
    })
    before = dict(case.strings.entries)
    checked = case.check()
    assert checked[2].ready and not checked[2].sync_review
    assert dict(checked[2].estimated_impact)["create_remote"] == 1
    assert not case.strings.calls
    assert case.execute(checked).is_success
    assert all(case.strings.entries[key] == value for key, value in before.items())
    created = case.strings.entries[100]
    assert case.gateway.files[created.file_id] == "source.esp.json"
    assert (created.original, created.translation) == (source.original, "本地译文")
    assert case.persisted_states()[source.identity].external_refs[0].opaque_id == 100
    calls = list(case.strings.calls)
    assert case.execute(case.check()).is_success
    assert case.strings.calls == calls
    assert bool(case.gateway.created_files) is not root_exists
    if not root_exists:
        assert case.gateway.created_files[0][2][0].context.startswith("00000000|")


def test_production_updates_only_fixed_root_candidate(roundtrip_case):
    case = roundtrip_case(size=1)
    source = case.sources[0]
    case.edit(source, "新的根目录译文")
    case.gateway.files[12] = "source.esp/物品.json"
    case.strings.entries.update({
        7: ParaTranzEntry(7, source.key, source.original, "根目录", "根上下文", 1, 11),
        8: ParaTranzEntry(8, source.key, source.original, "分类", "分类上下文", 1, 12),
    })
    untouched = case.strings.entries[8]
    assert case.execute(case.check()).is_success
    assert case.strings.entries[7].translation == "新的根目录译文"
    assert case.strings.entries[7].context == "根上下文"
    assert case.strings.entries[8] == untouched


def test_root_file_move_after_preflight_does_not_follow_id(roundtrip_case):
    case = roundtrip_case(size=1)
    source = case.sources[0]
    case.strings.entries[7] = ParaTranzEntry(7, source.key, source.original, "原译文", "原上下文", 1, 11)
    checked = case.check()
    case.gateway.files[11] = "moved/source.esp.json"
    assert not case.execute(checked).is_success
    assert case.strings.calls == []


def test_source_path_is_stable_for_single_batch_extensions_and_source_replacement():
    entries = tuple(make_test_collection(1))
    namespaces = (SourceNamespace("source:a"), SourceNamespace("source:b"), SourceNamespace("source:c"))
    collections = tuple(
        TranslationEntryCollection(replace(entry, entry_key=EntryKey(namespace, entry.key)) for entry in entries)
        for namespace in namespaces
    )
    sources = tuple(
        {"source_id": f"source-{index}", "namespace": ns.value, "location": path, "display_name": label}
        for index, (ns, path, label) in enumerate(
            zip(namespaces, ("C:/a/A.esp", "C:/b/A.esp", "C:/c/A.esm"), ("A.esp", "A.esp", "A.esm"), strict=True)
        )
    )
    context = SimpleNamespace(collection=collections[0], project_sources=sources)
    batch = tuple(ordinary_upload_path(context, collection) for collection in collections)
    assert len(set(batch)) == 3
    assert batch[0].startswith("A.esp_") and batch[0].endswith(".json")
    assert batch[2] == "A.esm.json"
    for collection, path in zip(collections, batch, strict=True):
        context.collection = collection
        assert ordinary_upload_path(context) == path
    context.project_sources = (dict(sources[0], location="D:/new/Z.esp"), *sources[1:])
    assert ordinary_upload_path(context, collections[0]) == batch[0]


def test_missing_source_name_is_blocked_instead_of_using_project_wide_scope():
    with pytest.raises(ValueError, match="固定上传路径"):
        ordinary_upload_path(SimpleNamespace(collection=make_test_collection(1)))


def test_disabled_same_name_source_does_not_change_enabled_sources_fixed_path():
    collection = make_test_collection(1)
    namespace = next(iter(collection)).identity.namespace.value
    context = SimpleNamespace(
        collection=collection,
        project_sources=(
            {"source_id": "active", "namespace": namespace, "display_name": "A.esp", "enabled": True},
            {"source_id": "disabled", "namespace": "disabled", "display_name": "A.esp", "enabled": False},
        ),
    )
    assert ordinary_upload_path(context) == "A.esp.json"


def test_disabled_registered_source_cannot_reenter_upload_through_legacy_slot():
    collection = make_test_collection(1)
    namespace = next(iter(collection)).identity.namespace.value
    context = SimpleNamespace(
        collection=collection,
        project_sources=({"source_id": "disabled", "namespace": namespace, "display_name": "A.esp", "enabled": False},),
        slots={"A.esp": SimpleNamespace(collection=collection, label="A.esp", esp_path="A.esp")},
    )
    with pytest.raises(ValueError, match="固定上传路径"):
        ordinary_upload_path(context)
