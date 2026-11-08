"""Binary EET preserves physical identities, native metadata and numeric caches."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase, WriteRequest
from transbridge.application.io.eet_adapter import EetAdapter
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.converter.eet_entries import document_entries, record_signature
from transbridge.parser.eet_binary_codec import EetDocument, EetRecord
from transbridge.parser.eet_number_cache import EetNumberCache, build_number_cache


def _record(**changes):
    values = dict(
        group="NPC_",
        form_id="00000800",
        editor_id="Npc",
        field="FULL",
        original="Original 12",
        translation="Translation 3",
        personal="Personal",
        index=0,
        status=-4,
        text_id=-7,
        comment="Native comment",
        original_hash=0xABCD1234,
        normalized_original="original12",
        normalized_hash=0x76543210,
        original_variables=EetNumberCache("Original %VAR00%", ("12",)).to_payload(),
        translation_variables=EetNumberCache("Custom cache %VAR00%", ("3",)).to_payload(),
        icon=37,
    )
    values.update(changes)
    return EetRecord(**values)


def _parse(tmp_path, *, records=None, type_dt=2):
    document = EetDocument(
        version=3,
        game="Skyrim SE" if type_dt == 1 else "",
        records=(_record(), _record()) if records is None else records,
        phrases=None if type_dt == 1 else (("A phrase", "短语"),),
        type_dt=type_dt,
    )
    source = tmp_path / "input.eet"
    source.write_bytes(document.to_bytes())
    parsed = TranslationIoUseCase().parse(
        ParseRequest(SourceDescriptor(str(source)), RequestContext("eet-test"), FormatId.BINARY_EET)
    )
    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    return source, document, parsed


def _write_request(tmp_path, parsed, entries=None, **changes):
    values = dict(
        target=SourceDescriptor(str(tmp_path / "output.eet")),
        format_id=FormatId.BINARY_EET,
        entries=parsed.entries if entries is None else entries,
        variant_revision=0,
        context=RequestContext("eet-test", run_id="eet-test-run"),
        source_snapshot=parsed.source_snapshot,
    )
    values.update(changes)
    return WriteRequest(**values)


@pytest.mark.parametrize("type_dt", [1, 2])
def test_unchanged_write_is_byte_identical_with_all_native_fields(tmp_path, type_dt):
    source, document, parsed = _parse(tmp_path, type_dt=type_dt)
    result = TranslationIoUseCase().write(_write_request(tmp_path, parsed))
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert (tmp_path / "output.eet").read_bytes() == source.read_bytes() == document.to_bytes()
    assert [entry.key for entry in parsed.entries] == ["eet-v1:0", "eet-v1:1"]
    assert dict(parsed.entries[0].metadata)["eet.form_id"] == "00000800"
    assert dict(parsed.source_snapshot.metadata)["eet_codec_version"] == 1


@pytest.mark.parametrize("translation", ["New 42 😀", ""])
def test_duplicate_native_records_change_independently_and_rebuild_only_translation_cache(tmp_path, translation):
    _, document, parsed = _parse(tmp_path)
    entry = replace(parsed.entries[1], translation=translation)
    result = EetAdapter().write(_write_request(tmp_path, parsed, (entry,)))
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    cache = build_number_cache(translation, group="NPC_", field="FULL")
    expected = replace(
        document,
        records=(
            document.records[0],
            replace(
                document.records[1], translation=translation, translation_variables=cache.to_payload() if cache else b""
            ),
        ),
    )
    assert EetDocument.from_bytes((tmp_path / "output.eet").read_bytes()) == expected


def test_signature_excludes_only_translation_and_its_cache():
    source = _record()
    assert record_signature(source) == record_signature(
        replace(source, translation="Changed", translation_variables=b"")
    )
    for changes in ({"personal": "Changed"}, {"status": 8}, {"original_variables": b""}, {"icon": None}):
        assert record_signature(source) != record_signature(replace(source, **changes))


@pytest.mark.parametrize(
    "forgery", ["namespace", "original", "duplicate", "ordinal", "signature", "sha", "old", "stage"]
)
def test_untrusted_entries_fail_before_output(tmp_path, forgery):
    _, _, parsed = _parse(tmp_path)
    entry = parsed.entries[0]
    entries = (entry,)
    if forgery == "namespace":
        entries = (replace(entry, entry_key=EntryKey(SourceNamespace("foreign"), entry.key)),)
    elif forgery == "original":
        entries = (replace(entry, original="Wrong original"),)
    elif forgery == "duplicate":
        entries = (entry, entry)
    elif forgery == "stage":
        entries = (replace(entry, stage=4),)
    else:
        metadata = dict(entry.metadata)
        if forgery == "ordinal":
            metadata["eet.record_index"] = True
        elif forgery == "signature":
            metadata["eet.record_signature"] = "wrong"
        elif forgery == "sha":
            metadata["eet.source_sha256"] = "0" * 64
        else:
            metadata.pop("eet.codec_version")
        entries = (replace(entry, metadata=tuple(metadata.items())),)
    result = EetAdapter().write(_write_request(tmp_path, parsed, entries))
    assert result.outcome is OperationOutcome.FAILED
    assert not (tmp_path / "output.eet").exists()


def test_hydrated_snapshot_survives_removed_source_and_live_source_conflict_is_rejected(tmp_path):
    source, document, parsed = _parse(tmp_path)
    source.write_bytes(b"externally modified")
    request = _write_request(tmp_path, parsed)
    assert EetAdapter().write(request).diagnostics[0].code == "SOURCE_FINGERPRINT_CONFLICT"
    source.unlink()
    assert EetAdapter().write(request).diagnostics[0].code == "SOURCE_SNAPSHOT_UNAVAILABLE"
    result = EetAdapter().write(replace(request, options=(("source_authority", "hydration-v2"),)))
    assert result.outcome is OperationOutcome.COMPLETED
    assert (tmp_path / "output.eet").read_bytes() == document.to_bytes()


@pytest.mark.parametrize("missing", ["snapshot", "version", "content"])
def test_missing_or_legacy_snapshot_fails(tmp_path, missing):
    _, _, parsed = _parse(tmp_path)
    snapshot = parsed.source_snapshot
    if missing == "snapshot":
        snapshot = None
    elif missing == "version":
        snapshot = replace(snapshot, metadata=())
    else:
        snapshot = replace(snapshot, content=None, lease_id="test-lease")
    changes = {"source_snapshot": snapshot}
    if snapshot is None:
        changes["new_template"] = b"unproven-new-eet"
    result = EetAdapter().write(_write_request(tmp_path, parsed, **changes))
    assert result.outcome is OperationOutcome.FAILED
    assert not (tmp_path / "output.eet").exists()


def test_locked_empty_translation_preserves_native_record(tmp_path):
    _, document, parsed = _parse(tmp_path)
    entry = replace(parsed.entries[0], stage=9, translation="")
    result = EetAdapter().write(_write_request(tmp_path, parsed, (entry,)))
    assert result.outcome is OperationOutcome.COMPLETED
    assert (tmp_path / "output.eet").read_bytes() == document.to_bytes()


@pytest.mark.parametrize("when", ["before", "after-render", "after-flush"])
def test_cancellation_keeps_target_and_cleans_owned_files(tmp_path, monkeypatch, when):
    import transbridge.application.io.eet_adapter as module

    _, _, parsed = _parse(tmp_path)
    target = tmp_path / "output.eet"
    target.write_bytes(b"previous")
    token = SimpleNamespace(is_cancelled=when == "before")
    adapter = EetAdapter()
    if when == "after-render":
        render = adapter.render_document

        def cancel_after_render(request):
            document = render(request)
            token.is_cancelled = True
            return document

        monkeypatch.setattr(adapter, "render_document", cancel_after_render)
    elif when == "after-flush":
        monkeypatch.setattr(module.os, "fsync", lambda _: setattr(token, "is_cancelled", True))
    result = adapter.write(_write_request(tmp_path, parsed, cancellation=token))
    assert result.outcome is OperationOutcome.CANCELLED
    assert target.read_bytes() == b"previous"
    assert not tuple(tmp_path.glob(".output.eet.*.tmp"))


def test_corrupt_input_returns_no_partial_entries(tmp_path):
    request = ParseRequest(SourceDescriptor(str(tmp_path / "bad.eet")), RequestContext("eet"), FormatId.BINARY_EET)
    result = EetAdapter().parse_bytes(request, EetDocument(3, "", (_record(),)).to_bytes()[:-1])
    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "EET_PARSE_FAILED"
    assert result.entries == ()
    assert result.source_snapshot is None


def test_shared_parse_bytes_uses_captured_content_and_namespace(tmp_path):
    source = SourceDescriptor(str(tmp_path / "does-not-exist.eet"))
    namespace = SourceNamespace("project-source")
    document = EetDocument(3, "", (_record(),))
    request = ParseRequest(source, RequestContext("eet"), FormatId.BINARY_EET, namespace)
    parsed = TranslationIoUseCase().parse_bytes(request, document.to_bytes())
    assert parsed.outcome is OperationOutcome.COMPLETED
    assert parsed.entries == document_entries(document, namespace)
    assert not (tmp_path / "does-not-exist.eet").exists()


def test_cancel_during_entry_mapping_returns_no_partial_entries(tmp_path):
    class Token:
        count = 0

        @property
        def is_cancelled(self):
            self.count += 1
            return self.count >= 3

    request = ParseRequest(
        SourceDescriptor(str(tmp_path / "cancel.eet")),
        RequestContext("eet"),
        FormatId.BINARY_EET,
        cancellation=Token(),
    )
    result = EetAdapter().parse_bytes(request, EetDocument(3, "", (_record(), _record())).to_bytes())
    assert result.outcome is OperationOutcome.CANCELLED
    assert result.entries == ()


def test_adapter_rejects_wrong_output_suffix_and_cleans_failed_replace(tmp_path, monkeypatch):
    import transbridge.application.io.eet_adapter as module

    _, _, parsed = _parse(tmp_path)
    wrong = _write_request(tmp_path, parsed, target=SourceDescriptor(str(tmp_path / "output.xml")))
    assert EetAdapter().write(wrong).diagnostics[0].code == "EET_TARGET_EXTENSION"
    target = tmp_path / "output.eet"
    target.write_bytes(b"existing")

    def fail(*_):
        raise OSError("injected replacement failure")

    monkeypatch.setattr(module.os, "replace", fail)
    result = EetAdapter().write(_write_request(tmp_path, parsed))
    assert result.outcome is OperationOutcome.FAILED
    assert "injected replacement" in result.diagnostics[0].message
    assert target.read_bytes() == b"existing"
    assert not tuple(tmp_path.glob(".output.eet.*.tmp"))
