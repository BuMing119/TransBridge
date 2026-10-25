"""Real BSA sources must survive hydration and publish a complete loose bundle."""

from dataclasses import replace
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from tests.bsa_fixtures import write_bsa
from tests.contracts.io.test_localized_hydrated_publication import _old_outputs, _run
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SsePluginAdapter, WriteRequest
from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWritePreflightService
from transbridge.application.io.publish import BackupPolicy, ConflictPolicy, OsPublishFilesystem
from transbridge.parser.strings_file import SkyrimStringsFile
from transbridge.ui.source_hydration import slot_from_hydration


def _field(kind, content):
    return struct.pack("<4sH", kind.encode(), len(content)) + content


def _record(kind, identifier, content, flags=0):
    return struct.pack("<4sIIIIHH", kind.encode(), len(content), flags, identifier, 0, 44, 0) + content


def _group(kind, content):
    return struct.pack("<4sI4sIHHHH", b"GRUP", len(content) + 24, kind.encode(), 0, 0, 0, 0, 0) + content


def _strings(records, suffix):
    directory, payload = [], b""
    for string_id, raw in records.items():
        directory.append(struct.pack("<II", string_id, len(payload)))
        raw += b"\0"
        payload += raw if suffix == "strings" else struct.pack("<I", len(raw)) + raw
    return struct.pack("<II", len(records), len(payload)) + b"".join(directory) + payload


@pytest.fixture(params=[("mod.esp", "mod.BSA"), ("Skyrim.esm", "Skyrim - Interface.BSA")])
def bsa_source(tmp_path, request):
    plugin_name, archive_name = request.param
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / plugin_name
    weapon = _record(
        "WEAP",
        0x800,
        _field("EDID", b"Weapon\0") + _field("FULL", struct.pack("<I", 1)) + _field("DESC", struct.pack("<I", 2)),
    ) + _record("WEAP", 0x801, _field("EDID", b"Unselected\0") + _field("FULL", struct.pack("<I", 4)))
    dialogue = _record("INFO", 0x802, _field("EDID", b"Dialogue\0") + _field("NAM1", struct.pack("<I", 3)))
    source.write_bytes(
        _record("TES4", 0, _field("HEDR", struct.pack("<fII", 1.7, 3, 0x803)), 0x80)
        + _group("WEAP", weapon)
        + _group("INFO", dialogue)
    )
    strings = {
        "strings": _strings({1: b"Original name", 4: b"Unselected name", 101: b"Caf\xe9 unreferenced"}, "strings"),
        "dlstrings": _strings({2: b"Original detail", 102: b"Unreferenced detail"}, "dlstrings"),
        "ilstrings": _strings({3: b"Original dialogue", 103: b"Unreferenced dialogue"}, "ilstrings"),
    }
    members = {f"strings/{source.stem.lower()}_english.{suffix}": content for suffix, content in strings.items()}
    members["textures/untouched.dds"] = b"unrelated original archive resource"
    archive = write_bsa(source_dir / archive_name, members)
    parsed = SsePluginAdapter().parse(
        ParseRequest(SourceDescriptor(str(source)), RequestContext("bsa-source"), FormatId.PLUGIN_SSE)
    )
    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    assert {entry.string_id: entry.original for entry in parsed.entries} == {
        1: "Original name",
        2: "Original detail",
        3: "Original dialogue",
        4: "Unselected name",
    }
    output = tmp_path / "output"
    output.mkdir()
    return SimpleNamespace(
        source=source,
        archive=archive,
        parsed=parsed,
        output=output,
        target=output / "translated.esp",
        strings=strings,
        members=members,
        original_plugin=source.read_bytes(),
        original_archive=archive.read_bytes(),
    )


def _preflight(case, *, overwrite=False):
    hydration = SimpleNamespace(
        location=str(case.source),
        format_id=FormatId.PLUGIN_SSE,
        source_snapshot=case.parsed.source_snapshot,
        entries=tuple(entry.snapshot() for entry in case.parsed.entries),
    )
    slot = slot_from_hydration(hydration)
    entries = tuple(
        replace(entry, translation=f"Translated {entry.string_id}", stage=1).snapshot()
        for entry in slot.collection
        if entry.string_id != 4
    )
    return HydratedWritePreflightService().preflight(
        HydratedWriteDraft(
            slot.source_snapshot,
            slot.format_id,
            entries,
            str(case.target),
            1,
            RequestContext("gui"),
            ConflictPolicy.EXPLICIT_OVERWRITE if overwrite else ConflictPolicy.FAIL,
            BackupPolicy.REQUIRED_IF_EXISTS,
        )
    )


def _assert_bundle(case, result):
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    expected = {
        case.target,
        *(case.output / "Strings" / f"translated_English.{suffix}" for suffix in case.strings),
    }
    assert {Path(path) for path in result.artifact_refs} == expected
    assert {path for path in case.output.rglob("*") if path.is_file()} == expected
    reparsed = SsePluginAdapter().parse(
        ParseRequest(SourceDescriptor(str(case.target)), RequestContext("verify-bsa"), FormatId.PLUGIN_SSE)
    )
    assert reparsed.outcome is OperationOutcome.COMPLETED, reparsed.diagnostics
    assert {entry.string_id: entry.original for entry in reparsed.entries} == {
        1: "Translated 1",
        2: "Translated 2",
        3: "Translated 3",
        4: "Unselected name",
    }
    for suffix, raw in case.strings.items():
        output = case.output / "Strings" / f"translated_English.{suffix}"
        original = SkyrimStringsFile.from_bytes(raw, "." + suffix)
        written = SkyrimStringsFile.from_file(output)
        assert len(written) == len(original)
        unused = {"strings": 101, "dlstrings": 102, "ilstrings": 103}[suffix]
        assert written.get(unused) == original.get(unused)
        # The unchanged cp1252 chunk must stay byte-for-byte intact as well.
        if suffix == "strings":
            assert b"Caf\xe9 unreferenced\0" in output.read_bytes()


def test_real_bsa_hydration_publishes_plugin_and_all_three_companions_without_touching_sources(bsa_source):
    checked = _preflight(bsa_source)
    assert checked.ready

    _assert_bundle(bsa_source, _run(checked))

    assert bsa_source.source.read_bytes() == bsa_source.original_plugin
    assert bsa_source.archive.read_bytes() == bsa_source.original_archive


def test_hydrated_preflight_rejects_the_source_archive_as_output_even_with_explicit_overwrite(bsa_source):
    case = SimpleNamespace(**{**vars(bsa_source), "target": bsa_source.archive})

    checked = _preflight(case, overwrite=True)

    assert next(item for item in checked.checks if item.code == "OVERWRITE_CONFIRMED").passed
    assert not checked.ready
    protection = next(item for item in checked.checks if item.code == "SOURCE_ARCHIVE_PROTECTED")
    assert not protection.passed and not protection.warning
    assert "BSA" in protection.message
    assert bsa_source.source.read_bytes() == bsa_source.original_plugin
    assert bsa_source.archive.read_bytes() == bsa_source.original_archive
    assert not tuple(bsa_source.output.iterdir())
    assert not tuple(bsa_source.source.parent.glob(".transbridge-publish-*"))


@pytest.mark.parametrize("removed", [False, True])
@pytest.mark.parametrize("changed_source", ["archive", "both"])
def test_hydrated_bsa_write_uses_captured_bytes_after_archive_and_plugin_change(bsa_source, removed, changed_source):
    checked = _preflight(bsa_source)
    assert checked.ready
    paths = (bsa_source.archive,) if changed_source == "archive" else (bsa_source.source, bsa_source.archive)
    for path in paths:
        path.unlink() if removed else path.write_bytes(b"unconfirmed source change")

    _assert_bundle(bsa_source, _run(checked))


@pytest.mark.parametrize("change", ["plugin", "archive", "missing_archive"])
def test_direct_nonhydrated_bsa_write_rejects_changed_sources(bsa_source, change):
    if change == "plugin":
        bsa_source.source.write_bytes(b"unconfirmed source change")
    elif change == "missing_archive":
        bsa_source.archive.unlink()
    else:
        members = dict(bsa_source.members)
        name = f"strings/{bsa_source.source.stem.lower()}_english.strings"
        members[name] = members[name].replace(b"Original name", b"Modified name")
        write_bsa(bsa_source.archive, members)
    result = SsePluginAdapter().write(
        WriteRequest(
            SourceDescriptor(str(bsa_source.target)),
            FormatId.PLUGIN_SSE,
            tuple(replace(entry, translation="Translated", stage=1) for entry in bsa_source.parsed.entries),
            1,
            RequestContext("direct-bsa"),
            source_snapshot=bsa_source.parsed.source_snapshot,
        )
    )

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code in {"SOURCE_FINGERPRINT_CONFLICT", "SOURCE_SNAPSHOT_UNAVAILABLE"}
    assert not tuple(bsa_source.output.iterdir())


def test_bsa_companion_existing_on_its_own_requires_overwrite_confirmation(bsa_source):
    originals = _old_outputs(bsa_source)
    bsa_source.target.unlink()

    checked = _preflight(bsa_source)

    assert not checked.ready
    assert not next(item for item in checked.checks if item.code == "OVERWRITE_CONFIRMED").passed
    assert all(path.read_bytes() == content for path, content in originals.items() if path != bsa_source.target)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("after_replace", [False, True])
def test_bsa_bundle_failure_on_final_plugin_replace_restores_every_output(bsa_source, existing, after_replace):
    originals = _old_outputs(bsa_source) if existing else {}
    checked = _preflight(bsa_source, overwrite=existing)
    assert checked.ready

    class FailingFilesystem(OsPublishFilesystem):
        failed = False

        def atomic_replace(self, source, destination):
            if Path(destination) == bsa_source.target and not self.failed:
                self.failed = True
                if after_replace:
                    super().atomic_replace(source, destination)
                raise OSError("injected BSA bundle final plugin failure")
            super().atomic_replace(source, destination)

    result = _run(checked, FailingFilesystem())

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "BUNDLE_COMMIT_FAILED"
    assert {path for path in bsa_source.output.rglob("*") if path.is_file()} == set(originals)
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert bsa_source.source.read_bytes() == bsa_source.original_plugin
    assert bsa_source.archive.read_bytes() == bsa_source.original_archive
    assert not tuple(bsa_source.output.glob(".transbridge-publish-*"))
