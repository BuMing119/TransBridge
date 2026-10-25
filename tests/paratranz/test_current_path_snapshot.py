"""Downloads resolve current file IDs without upload-time bindings."""

from types import SimpleNamespace

import pytest

from tests.paratranz.test_file_publication import gateway
from transbridge.application.io.identity import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.sync import SyncOperation
from transbridge.application.sync.models import LocalEntrySnapshot
from transbridge.paratranz.path_snapshot import CurrentPathSnapshotAdapter
from transbridge.ui.operations.categorized_download_scope import download_snapshot_adapter

NS = SourceNamespace("source:a")


def test_reads_only_current_plugin_files_with_one_complete_request_each():
    rows = [{"key": "same", "original": "Original", "translation": "译文", "stage": 3, "context": "NPC_:FULL"}]
    port, session = gateway(
        [
            {"id": 20, "name": "A.esp/人名.json"},
            {"id": 30, "name": "B.esp/人名.json"},
            {"id": 40, "name": "A.esp_1.json"},
            {"id": 50, "name": "Other/A.esp.json"},
        ],
        rows,
        rows,
    )
    adapter = CurrentPathSnapshotAdapter(
        port.config, "A.esp.json", category_paths=("A.esp/人名.json",), gateway_factory=lambda _config: port
    )
    snapshots = adapter.fetch(7, NS, limit=100)
    assert len(snapshots) == 2
    assert all(row.external_ref is None for row in snapshots)
    assert all(row.translation == "译文" for row in snapshots)
    assert [call.path_url for call in session.calls] == [
        "/api/projects/7/files",
        "/api/projects/7/files/20/translation",
        "/api/projects/7/files/40/translation",
    ]


def test_recreated_file_is_read_using_current_id():
    port, session = gateway(
        [{"id": 99, "name": "A.esp/任务/对话_[Quest].json"}],
        [{"key": "quest", "original": "Original", "translation": "新文件译文", "stage": 1}],
    )
    adapter = CurrentPathSnapshotAdapter(
        port.config,
        "A.esp.json",
        category_paths=("A.esp/任务/对话_[Quest].json",),
        gateway_factory=lambda _config: port,
    )
    assert adapter.fetch(7, NS, limit=10)[0].translation == "新文件译文"
    assert session.calls[-1].path_url == "/api/projects/7/files/99/translation"


def test_complete_file_response_cannot_exceed_snapshot_limit():
    port, _session = gateway([{"id": 20, "name": "A.esp.json"}], [{"key": str(i), "original": "O"} for i in range(3)])
    adapter = CurrentPathSnapshotAdapter(port.config, "A.esp.json", gateway_factory=lambda _config: port)
    with pytest.raises(ValueError, match="limit"):
        adapter.fetch(7, NS, limit=2)


def test_project_download_uses_paths_without_reading_old_binding_service():
    def forbidden(*args, **kwargs):
        raise AssertionError("download must not require persisted upload bindings")

    context = SimpleNamespace(
        uses_authoritative_projection=True,
        config=object(),
        project_sources=({"source_id": "a", "location": "A.esp", "display_name": "A.esp", "namespace": NS.value},),
        project_relations=(),
        project_commands=SimpleNamespace(categorized_upload_file_scope=forbidden),
        slots={},
    )
    request = SimpleNamespace(
        ui_context=context,
        operation=SyncOperation.DOWNLOAD,
        namespace=NS,
        local_entries=(LocalEntrySnapshot(EntryKey(NS, "same"), EntryRevision(), "Original", ""),),
    )
    adapter = download_snapshot_adapter(object(), request)
    assert isinstance(adapter, CurrentPathSnapshotAdapter)
    assert adapter.root_path == "A.esp.json"


def test_root_only_scope_never_reads_a_plugin_directory_without_explicit_current_categories():
    port, session = gateway([{"id": 20, "name": "A.esp/人名.json"}, {"id": 30, "name": "A.esp/任务/对话_[Old].json"}])
    adapter = CurrentPathSnapshotAdapter(port.config, "A.esp.json", gateway_factory=lambda _config: port)
    assert adapter.fetch(7, NS, limit=10) == ()
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "rows, message",
    [
        ({"items": []}, "词条数据无效"),
        ([{"key": "same", "original": "Original", "fileId": 99}], "以外"),
        ([{"id": 1, "key": "a", "original": "O"}, {"id": 1, "key": "b", "original": "O"}], "重复词条 ID"),
    ],
)
def test_file_response_validation_never_uses_partial_or_unowned_entries(rows, message):
    port, session = gateway([{"id": 20, "name": "A.esp.json"}], rows)
    adapter = CurrentPathSnapshotAdapter(port.config, "A.esp.json", gateway_factory=lambda _config: port)
    with pytest.raises(ValueError, match=message):
        adapter.fetch(7, NS, limit=10)
    assert session.calls[-1].path_url == "/api/projects/7/files/20/translation"


@pytest.mark.parametrize("path", ["../A.json", "/A.json", "A\\B.json", "A//B.json", "A.txt"])
def test_invalid_expected_paths_are_rejected_before_remote_reads(path):
    with pytest.raises(ValueError, match="路径无效"):
        CurrentPathSnapshotAdapter(object(), "A.esp.json", category_paths=(path,))


def _registered_request(config, labels=("A.esp",), *, disabled=()):
    return SimpleNamespace(
        ui_context=SimpleNamespace(
            config=config,
            project_sources=tuple(
                {
                    "source_id": f"registered-{index}",
                    "location": f"C:/Data/{label}",
                    "display_name": label,
                    "namespace": NS.value if index == 0 else f"source:other-{index}",
                    "enabled": index not in disabled,
                }
                for index, label in enumerate(labels)
            ),
            project_relations=(),
            slots={},
        ),
        operation=SyncOperation.DOWNLOAD,
        namespace=NS,
        local_entries=(LocalEntrySnapshot(EntryKey(NS, "same"), EntryRevision(), "Original", "", "NPC_:FULL"),),
    )


def _project_adapter(config, labels=("A.esp",), **options):
    return download_snapshot_adapter(object(), _registered_request(config, labels, **options))


def _rows(translation="Legacy translation"):
    return [{"key": "same", "original": "Original", "translation": translation, "stage": 3}]


def test_single_source_project_reads_unambiguous_old_root_without_changing_upload_name():
    port, session = gateway([{"id": 10, "name": "A.json"}, {"id": 20, "name": "B.json"}], _rows())
    adapter = _project_adapter(port.config, ("A.esp", "B.esp"))
    adapter.gateway_factory = lambda _config: port

    assert adapter.root_path == "A.esp.json"
    assert adapter.fetch(7, NS, limit=10)[0].translation == "Legacy translation"
    assert [call.path_url for call in session.calls] == [
        "/api/projects/7/files",
        "/api/projects/7/files/10/translation",
    ]


@pytest.mark.parametrize("current_file", ["A.esp.json", "A.esp/人名.json", "A.esp_2.json"])
def test_any_current_layout_file_prevents_mixing_with_old_root_or_category(current_file):
    port, session = gateway(
        [
            {"id": 10, "name": "A.json"},
            {"id": 11, "name": "A/人名.json"},
            {"id": 20, "name": current_file},
        ],
        _rows("Current translation"),
    )
    adapter = _project_adapter(port.config)
    adapter.gateway_factory = lambda _config: port

    snapshots = adapter.fetch(7, NS, limit=10)

    assert len(snapshots) == 1 and snapshots[0].translation == "Current translation"
    assert session.calls[-1].path_url == "/api/projects/7/files/20/translation"
    assert len(session.calls) == 2


def test_old_classification_and_numeric_partitions_stay_within_current_file_whitelist():
    port, session = gateway(
        [
            {"id": 10, "name": "A/人名.json"},
            {"id": 11, "name": "A/人名_3.json"},
            {"id": 12, "name": "A_2.json"},
            {"id": 20, "name": "A/任务/对话_[OtherQuest].json"},
            {"id": 21, "name": "A/无关.json"},
            {"id": 22, "name": "Other/A.json"},
            {"id": 23, "name": "A/人名_backup.json"},
        ],
        _rows(),
        _rows(),
        _rows(),
    )
    adapter = _project_adapter(port.config)
    adapter.gateway_factory = lambda _config: port

    assert len(adapter.fetch(7, NS, limit=10)) == 3
    assert [call.path_url for call in session.calls[1:]] == [
        "/api/projects/7/files/10/translation",
        "/api/projects/7/files/11/translation",
        "/api/projects/7/files/12/translation",
    ]


@pytest.mark.parametrize("filename", ["a.JSON", "a.ESP.JSON", "a.ESP/人名_2.JSON"])
def test_unique_case_insensitive_path_is_read_with_actual_inventory_spelling(filename):
    port, session = gateway([{"id": 10, "name": filename}], _rows())
    adapter = _project_adapter(port.config)
    adapter.gateway_factory = lambda _config: port

    assert adapter.fetch(7, NS, limit=10)[0].translation == "Legacy translation"
    assert session.calls[-1].path_url == "/api/projects/7/files/10/translation"


def test_exact_case_wins_over_an_alternate_case_file_in_the_same_generation():
    port, session = gateway([{"id": 10, "name": "a.ESP.JSON"}, {"id": 11, "name": "A.esp.json"}], _rows("Exact"))
    adapter = _project_adapter(port.config)
    adapter.gateway_factory = lambda _config: port

    assert adapter.fetch(7, NS, limit=10)[0].translation == "Exact"
    assert session.calls[-1].path_url == "/api/projects/7/files/11/translation"


def test_multiple_case_only_legacy_variants_without_exact_match_require_disambiguation():
    port, session = gateway([{"id": 10, "name": "a.JSON"}, {"id": 11, "name": "a.JsOn"}])
    adapter = _project_adapter(port.config)
    adapter.gateway_factory = lambda _config: port

    with pytest.raises(ValueError, match="大小写"):
        adapter.fetch(7, NS, limit=10)
    assert len(session.calls) == 1


@pytest.mark.parametrize("labels", [("A.esp", "A.esm"), ("A.esp", "A")])
def test_unselected_registered_source_can_make_an_old_name_ambiguous(labels):
    port, session = gateway([{"id": 10, "name": "A.json"}])
    adapter = _project_adapter(port.config, labels)
    adapter.gateway_factory = lambda _config: port

    with pytest.raises(ValueError, match="多个插件"):
        adapter.fetch(7, NS, limit=10)
    assert len(session.calls) == 1


def test_disabled_same_stem_source_does_not_block_a_unique_legacy_download():
    port, _session = gateway([{"id": 10, "name": "A.json"}], _rows())
    adapter = _project_adapter(port.config, ("A.esp", "A.esm"), disabled=(1,))
    adapter.gateway_factory = lambda _config: port

    assert adapter.fetch(7, NS, limit=10)[0].translation == "Legacy translation"


def test_current_path_still_works_when_the_legacy_name_is_ambiguous():
    port, session = gateway([{"id": 10, "name": "A.json"}, {"id": 20, "name": "A.esp.json"}], _rows("Current"))
    adapter = _project_adapter(port.config, ("A.esp", "A.esm"))
    adapter.gateway_factory = lambda _config: port

    assert adapter.fetch(7, NS, limit=10)[0].translation == "Current"
    assert session.calls[-1].path_url == "/api/projects/7/files/20/translation"


def test_headless_explicit_root_without_registry_retains_safe_single_source_fallback():
    port, session = gateway([{"id": 10, "name": "A.json"}], _rows())
    request = _registered_request(port.config)
    request.ui_context.project_sources = ()
    request.upload_path = "A.esp.json"
    adapter = download_snapshot_adapter(object(), request)
    adapter.gateway_factory = lambda _config: port

    assert adapter.fetch(7, NS, limit=10)[0].translation == "Legacy translation"
    assert session.calls[-1].path_url == "/api/projects/7/files/10/translation"


def test_registered_namespace_allows_an_explicit_noncanonical_root():
    port, session = gateway(
        [{"id": 10, "name": "Custom.json"}, {"id": 20, "name": "A.esp.json"}], _rows("Custom translation")
    )
    request = _registered_request(port.config)
    request.upload_path = "Custom.esp.json"
    adapter = download_snapshot_adapter(object(), request)
    adapter.gateway_factory = lambda _config: port

    assert adapter.root_path == "Custom.esp.json"
    assert adapter.fetch(7, NS, limit=10)[0].translation == "Custom translation"
    assert session.calls[-1].path_url == "/api/projects/7/files/10/translation"


def test_explicit_registered_scope_still_checks_other_sources_for_legacy_ambiguity():
    port, session = gateway([{"id": 10, "name": "Custom.json"}])
    request = _registered_request(port.config, ("A.esp", "Custom.esm"))
    request.upload_path = "Custom.esp.json"
    adapter = download_snapshot_adapter(object(), request)
    adapter.gateway_factory = lambda _config: port

    with pytest.raises(ValueError, match="多个插件"):
        adapter.fetch(7, NS, limit=10)
    assert len(session.calls) == 1


def test_explicit_root_without_registered_namespace_owner_remains_readable():
    port, session = gateway([{"id": 10, "name": "Custom.esp.json"}], _rows("Explicit root"))
    request = _registered_request(port.config)
    request.ui_context.project_sources[0]["namespace"] = "source:another-registered-source"
    request.upload_path = "Custom.esp.json"
    adapter = download_snapshot_adapter(object(), request)
    adapter.gateway_factory = lambda _config: port

    assert adapter.fetch(7, NS, limit=10)[0].translation == "Explicit root"
    assert session.calls[-1].path_url == "/api/projects/7/files/10/translation"


def test_explicit_unknown_scope_respects_legacy_names_reserved_by_registered_sources():
    port, session = gateway([{"id": 10, "name": "Custom.json"}])
    request = _registered_request(port.config, ("Custom",))
    request.ui_context.project_sources[0]["namespace"] = "source:another-registered-source"
    request.upload_path = "Custom.esp.json"
    adapter = download_snapshot_adapter(object(), request)
    adapter.gateway_factory = lambda _config: port

    with pytest.raises(ValueError, match="多个插件"):
        adapter.fetch(7, NS, limit=10)
    assert len(session.calls) == 1


def test_namespace_less_old_registration_can_be_identified_by_its_canonical_root():
    port, session = gateway([{"id": 10, "name": "A.json"}], _rows("Canonical fallback"))
    request = _registered_request(port.config)
    request.ui_context.project_sources[0].pop("namespace")
    request.ui_context.project_sources[0]["bilingual_capability"] = "none"
    request.upload_path = "A.esp.json"
    adapter = download_snapshot_adapter(object(), request)
    adapter.gateway_factory = lambda _config: port

    assert adapter.fetch(7, NS, limit=10)[0].translation == "Canonical fallback"
    assert session.calls[-1].path_url == "/api/projects/7/files/10/translation"
