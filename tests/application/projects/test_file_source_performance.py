from dataclasses import fields, replace
import hashlib
from io import BytesIO
import os
import struct

import pytest

from tests.application.projects.test_categorized_upload import project as project_fixture
from tests.plugin_fixtures import _field, _record, write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects.provisioning import ProjectSourceRequest
from transbridge.application.projects.source_hydration_cache import SourceHydrationCache, SourceReadHydration
from transbridge.parser.strings_file import PluginStringsLookup, SkyrimStringsWriter
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer

project = project_fixture


def _snapshot(service, context, ids):
    return service.snapshot(context, endpoint="https://paratranz.cn", remote_project_id=42, source_ids=ids)


def test_catalog_uses_only_registry_and_variant_counts_even_if_source_files_are_unavailable(project, monkeypatch):
    services, service, context, first, second = project
    first.unlink()
    second.unlink()
    monkeypatch.setattr(
        service._source_preparer, "prepare_source", lambda *_args, **_kwargs: pytest.fail("Unexpected parse")
    )

    result = service.catalog(context)
    facade_result = services.gui_project_commands.file_sources_catalog(context)

    assert result.is_success and facade_result.is_success
    assert result.value == facade_result.value
    assert {item.label for item in result.value} == {first.name, second.name}
    assert [item.count for item in result.value] == [1, 1]
    assert len({item.namespace for item in result.value}) == 2


def test_repeated_selected_capture_parses_once_and_overlays_fresh_unsaved_state(project, monkeypatch):
    services, service, context, *_ = project
    selected = service.catalog(context).value[0]
    calls = []
    original = service._source_preparer.prepare_source

    def prepare(request, *args, **kwargs):
        calls.append(request.location)
        return original(request, *args, **kwargs)

    monkeypatch.setattr(service._source_preparer, "prepare_source", prepare)
    first = _snapshot(service, context, (selected.source_id,))
    assert first.is_success, first.diagnostics
    key = first.value.sources[0].entries[0].identity
    assert services.gui_project_commands.update_entry(
        key, context, translation="New unsaved translation", stage=3
    ).is_success

    second = _snapshot(service, context, (selected.source_id,))
    third = _snapshot(service, context, (selected.source_id,))

    assert second.is_success and third.is_success
    assert len(calls) == 1
    assert first.value.sources[0].entries[0].translation == ""
    assert second.value.sources[0].entries[0].translation == "New unsaved translation"
    assert second.value.sources[0].entries[0].stage == 3
    assert third.value.sources[0].entries[0] is not second.value.sources[0].entries[0]
    assert len(second.value.sources) == 1 and len(second.value.source_names) == 2


def test_hash_verification_detects_source_change_with_identical_size_and_restored_mtime(project):
    _services, service, context, first, _second = project
    selected = next(item for item in service.catalog(context).value if item.label == first.name)
    assert _snapshot(service, context, (selected.source_id,)).is_success
    previous = first.stat()
    content = first.read_bytes()
    assert b"Alice" in content
    first.write_bytes(content.replace(b"Alice", b"Other"))
    os.utime(first, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert first.stat().st_size == previous.st_size

    result = _snapshot(service, context, (selected.source_id,))

    assert not result.is_success
    assert result.diagnostics[0].code == "PROJECT_SOURCE_FINGERPRINT_MISMATCH"


class CountingPreparer:
    def __init__(self):
        self.inner = TranslationIoProjectSourcePreparer()
        self.calls = 0

    def prepare_source(self, *args, **kwargs):
        self.calls += 1
        return self.inner.prepare_source(*args, **kwargs)


def _request(path, **options):
    return ProjectSourceRequest(
        str(path),
        FormatId.PLUGIN_SSE,
        hashlib.sha256(path.read_bytes()).hexdigest(),
        tuple({"source_namespace": "source:cache-test", **options}.items()),
    )


def _localized(path):
    record = _record(b"NPC_", 0x800, _field(b"EDID", "Npc") + b"FULL\x04\x00" + struct.pack("<I", 1))
    header = bytearray(_record(b"TES4", 0, b""))
    header[8:12] = struct.pack("<I", 0x80)
    path.write_bytes(header + struct.pack("<4sI4siHHI", b"GRUP", 24 + len(record), b"NPC_", 0, 0, 0, 0) + record)
    return path


def test_loose_strings_content_change_invalidates_cached_original_even_with_same_size_and_mtime(tmp_path):
    path = _localized(tmp_path / "localized.esp")
    directory = tmp_path / "Strings"
    directory.mkdir()
    companion = directory / "localized_English.strings"
    companion.write_bytes(SkyrimStringsWriter.to_bytes({1: "Alice"}, False))
    cache, preparer, context = SourceHydrationCache(), CountingPreparer(), RequestContext("cache")
    request = _request(path)
    first = cache.prepare(preparer, request, context, scope_key="project")
    assert [entry.original for entry in first.entries] == ["Alice"]
    stamp = companion.stat()
    companion.write_bytes(SkyrimStringsWriter.to_bytes({1: "Other"}, False))
    os.utime(companion, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    second = cache.prepare(preparer, request, context, scope_key="project")

    assert [entry.original for entry in second.entries] == ["Other"]
    assert preparer.calls == 2


def test_new_string_companion_and_changed_parse_options_invalidate_a_cached_parse(tmp_path):
    path = write_plugin(tmp_path / "plain.esp", [(0x800, "Npc", "Alice")])
    cache, preparer, context = SourceHydrationCache(), CountingPreparer(), RequestContext("cache")
    request = _request(path)
    cache.prepare(preparer, request, context, scope_key="project")
    directory = tmp_path / "Strings"
    directory.mkdir()
    companion = directory / "plain_English.strings"
    companion.write_bytes(SkyrimStringsWriter.to_bytes({1: "Alice"}, False))
    cache.prepare(preparer, request, context, scope_key="project")
    cache.prepare(preparer, _request(path, discover_sibling_strings=False), context, scope_key="project")

    assert preparer.calls == 3


def test_bsa_dependency_uses_canonical_parser_selection_and_matching_member_contents(tmp_path, monkeypatch):
    from sse_bsa import bsa_archive

    path = _localized(tmp_path / "CustomBase.esm")
    (tmp_path / "Skyrim - Interface.bsa").write_bytes(b"synthetic archive handle")
    monkeypatch.setattr(PluginStringsLookup, "_BASE_GAME_PLUGINS", frozenset({"custombase"}))
    payload = {"strings/custombase_english.strings": SkyrimStringsWriter.to_bytes({1: "Alice"}, False)}

    class Archive:
        def __init__(self, archive_path):
            assert archive_path.name == "Skyrim - Interface.bsa"

        def glob(self, _pattern):
            return tuple(payload)

        def get_file_stream(self, member):
            return BytesIO(payload[member])

    monkeypatch.setattr(bsa_archive, "BSAArchive", Archive)
    cache, preparer, context = SourceHydrationCache(), CountingPreparer(), RequestContext("cache")
    request = _request(path)
    first = cache.prepare(preparer, request, context, scope_key="project")
    payload["strings/custombase_english.strings"] = SkyrimStringsWriter.to_bytes({1: "Other"}, False)

    second = cache.prepare(preparer, request, context, scope_key="project")

    assert [entry.original for entry in first.entries] == ["Alice"]
    assert [entry.original for entry in second.entries] == ["Other"]
    assert preparer.calls == 2


def test_cached_model_retains_no_source_bytes_and_obeys_estimated_memory_budget(tmp_path):
    path = write_plugin(tmp_path / "plain.esp", [(0x800, "Npc", "Alice")])
    cache, preparer, context = SourceHydrationCache(max_bytes=1), CountingPreparer(), RequestContext("cache")
    request = _request(path)
    first = cache.prepare(preparer, request, context, scope_key="project")
    second = cache.prepare(preparer, request, context, scope_key="project")

    assert isinstance(first, SourceReadHydration)
    assert {field.name for field in fields(first)} == {"fingerprint", "entries"}
    assert first == second and preparer.calls == 2
    assert cache._estimated_bytes == cache._entry_count == len(cache._items) == 0


def test_midparse_dependency_change_is_never_published_or_cached(tmp_path):
    from transbridge.application.contracts import DomainError

    path = write_plugin(tmp_path / "plain.esp", [(0x800, "Npc", "Alice")])
    cache, preparer, context = SourceHydrationCache(), CountingPreparer(), RequestContext("cache")
    request = _request(path)
    original = preparer.inner.prepare_source

    def change_during_parse(*args, **kwargs):
        prepared = original(*args, **kwargs)
        path.write_bytes(path.read_bytes().replace(b"Alice", b"Other"))
        return prepared

    preparer.inner.prepare_source = change_during_parse
    with pytest.raises(DomainError, match="读取期间变化"):
        cache.prepare(preparer, request, context, scope_key="project")
    assert not cache._items


@pytest.mark.parametrize("change", ["owner", "permissions", "roots"])
def test_cache_is_partitioned_by_request_authority(tmp_path, change):
    path = write_plugin(tmp_path / "plain.esp", [(0x800, "Npc", "Alice")])
    cache, preparer = SourceHydrationCache(), CountingPreparer()
    context = RequestContext("owner-a", permissions=frozenset({"read"}), authorized_roots=(str(tmp_path),))
    request = _request(path)
    cache.prepare(preparer, request, context, scope_key="same-project")
    changed = {
        "owner": replace(context, owner_id="owner-b"),
        "permissions": replace(context, permissions=frozenset()),
        "roots": replace(context, authorized_roots=(str(tmp_path), str(tmp_path))),
    }[change]

    cache.prepare(preparer, request, changed, scope_key="same-project")

    assert preparer.calls == 2


def test_unauthorized_request_is_rejected_before_signature_read_or_cache_lookup(tmp_path, monkeypatch):
    from transbridge.application.contracts import DomainError
    from transbridge.application.projects import source_hydration_cache as module

    allowed, denied = tmp_path / "allowed", tmp_path / "denied"
    allowed.mkdir()
    denied.mkdir()
    path = write_plugin(denied / "plain.esp", [(0x800, "Npc", "Alice")])
    cache, preparer = SourceHydrationCache(), CountingPreparer()
    request = _request(path)
    cache.prepare(preparer, request, RequestContext("same-owner"), scope_key="same-project")
    monkeypatch.setattr(module, "_file_hash", lambda _path: pytest.fail("Unauthorized source was opened"))

    with pytest.raises(DomainError, match="超出授权范围"):
        cache.prepare(
            preparer,
            request,
            RequestContext("same-owner", authorized_roots=(str(allowed),)),
            scope_key="same-project",
        )
    assert preparer.calls == 1


def test_string_dependency_outside_explicit_file_grant_is_not_read_or_parsed(tmp_path, monkeypatch):
    from transbridge.application.contracts import DomainError
    from transbridge.application.projects import source_hydration_cache as module

    path = write_plugin(tmp_path / "plain.esp", [(0x800, "Npc", "Alice")])
    strings = tmp_path / "Strings"
    strings.mkdir()
    companion = strings / "plain_English.strings"
    companion.write_bytes(SkyrimStringsWriter.to_bytes({1: "Alice"}, False))
    real_hash, opened = module._file_hash, []

    def hash_allowed_only(input_path):
        assert input_path != companion
        opened.append(input_path)
        return real_hash(input_path)

    request = _request(path)
    cache, preparer = SourceHydrationCache(), CountingPreparer()
    monkeypatch.setattr(module, "_file_hash", hash_allowed_only)
    with pytest.raises(DomainError, match="超出授权范围"):
        cache.prepare(
            preparer,
            request,
            RequestContext("owner", authorized_roots=(str(path),)),
            scope_key="project",
        )
    assert opened == [path]
    assert preparer.calls == 0
