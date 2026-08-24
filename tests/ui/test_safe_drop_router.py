from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from transbridge.fileops.archive_policy import ArchiveBudget, ArchiveManifest, ArchiveMember, ArchiveMemberType
from transbridge.ui.drop_router import (
    DropBudget,
    DropKind,
    DropResolutionStatus,
    DropRouter,
)
from transbridge.ui.shell.action_catalog import IntentId


def _write(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


@pytest.mark.parametrize(
    ("name", "content", "kind", "format_id"),
    (
        ("Demo.esp", b"TES4" + b"\0" * 12, DropKind.PLUGIN, "plugin.sse"),
        ("translated.eet", b"EET_" + b"\0" * 12, DropKind.EET, "binary.eet"),
        ("translated.xml", b"<DocumentElement/>", DropKind.EET, "xml.eet"),
        ("translated.xml", b"<SSTXMLRessources/>", DropKind.XT, "xml.xt"),
        ("translated.sst", b"SSU8" + b"\0" * 12, DropKind.SST, "sst.ssu8"),
        ("translated.json", b'[{"key":"a","original":"A"}]', DropKind.JSON, "json.paratranz"),
    ),
)
def test_supported_file_signatures_only_propose_canonical_intents(
    tmp_path: Path,
    name: str,
    content: bytes,
    kind: DropKind,
    format_id: str,
) -> None:
    source = _write(tmp_path / name, content)

    result = DropRouter().resolve((source,))

    assert result.status is DropResolutionStatus.CANDIDATE
    assert result.items[0].kind is kind
    assert result.candidate is not None
    expected = IntentId.SOURCE_PARSE if kind is DropKind.PLUGIN else IntentId.SOURCE_MIGRATE
    assert result.candidate.intent_id is expected
    assert result.candidate.requires_confirmation
    assert result.candidate.payload_mapping()["format_id"] == format_id
    assert result.candidate.payload_mapping()["path"] == str(source.resolve())


def test_transbridge_archive_is_safely_inspected_and_only_proposed(tmp_path: Path) -> None:
    archive_path = tmp_path / "Demo.transbridge"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("project.json", "{}")
        archive.writestr("variants/default.json", "{}")

    result = DropRouter().resolve((archive_path,))

    assert result.status is DropResolutionStatus.CANDIDATE
    assert result.items[0].kind is DropKind.PROJECT_ARCHIVE
    assert result.candidate is not None
    assert result.candidate.intent_id is IntentId.PROJECT_IMPORT
    assert not (tmp_path / "project.json").exists(), "inspection must not extract the archive"


def test_fomod_archive_requires_marker_and_only_proposes_operation_plan(tmp_path: Path) -> None:
    archive_path = tmp_path / "Demo.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Demo/fomod/ModuleConfig.xml", "<config/>")

    result = DropRouter().resolve((archive_path,))

    assert result.status is DropResolutionStatus.CANDIDATE
    assert result.items[0].kind is DropKind.FOMOD_ARCHIVE
    assert result.candidate is not None
    assert result.candidate.intent_id is IntentId.PUBLISH_FOMOD
    assert not (tmp_path / "Demo").exists(), "inspection must not extract or publish"


def test_zip_without_fomod_marker_has_recoverable_feedback(tmp_path: Path) -> None:
    archive_path = tmp_path / "assets.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("readme.txt", "hello")

    result = DropRouter().resolve((archive_path,))

    assert result.status is DropResolutionStatus.REJECTED
    assert result.candidate is None
    assert result.diagnostics[0].code == "DROP_ARCHIVE_NOT_FOMOD"
    assert result.diagnostics[0].recovery


def test_strings_directory_is_recognised_without_parsing_or_mutating_files(tmp_path: Path) -> None:
    strings = tmp_path / "Strings"
    strings.mkdir()
    original = b"not parsed during drop"
    source = _write(strings / "Demo_English.strings", original)

    result = DropRouter().resolve((strings,))

    assert result.status is DropResolutionStatus.CANDIDATE
    assert result.items[0].kind is DropKind.STRINGS_DIRECTORY
    assert result.candidate is not None
    assert result.candidate.intent_id is IntentId.SOURCE_MIGRATE
    assert source.read_bytes() == original


def test_ambiguous_json_requires_explicit_format_choice(tmp_path: Path) -> None:
    source = _write(tmp_path / "empty.json", b"[]")

    result = DropRouter().resolve((source,))

    assert result.status is DropResolutionStatus.NEEDS_CHOICE
    assert result.candidate is None
    assert result.items[0].kind is DropKind.AMBIGUOUS
    assert result.diagnostics[0].code == "DROP_FORMAT_AMBIGUOUS"


def test_unknown_and_mixed_inputs_never_produce_a_candidate(tmp_path: Path) -> None:
    unknown = _write(tmp_path / "notes.txt", b"hello")
    plugin = _write(tmp_path / "Demo.esp", b"TES4" + b"\0" * 12)
    translation = _write(tmp_path / "translation.xml", b"<DocumentElement/>")

    unknown_result = DropRouter().resolve((unknown,))
    mixed_result = DropRouter().resolve((plugin, translation))

    assert unknown_result.status is DropResolutionStatus.REJECTED
    assert unknown_result.diagnostics[0].code == "DROP_FORMAT_UNSUPPORTED"
    assert mixed_result.status is DropResolutionStatus.NEEDS_CHOICE
    assert mixed_result.candidate is None
    assert mixed_result.diagnostics[0].code == "DROP_MIXED_INPUTS"


def test_probe_budget_rejects_large_file_before_reading_it(tmp_path: Path, monkeypatch) -> None:
    source = _write(tmp_path / "large.json", b" " * 33)
    opened: list[Path] = []
    original_open = Path.open

    def tracking_open(path: Path, *args, **kwargs):
        opened.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)

    result = DropRouter(budget=DropBudget(max_probe_bytes=32)).resolve((source,))

    assert result.status is DropResolutionStatus.REJECTED
    assert result.diagnostics[0].code == "DROP_PROBE_SIZE_LIMIT"
    assert opened == []


def test_large_plugin_uses_bounded_signature_instead_of_rejecting_normal_source(tmp_path: Path) -> None:
    source = _write(tmp_path / "large.esp", b"TES4" + b"\0" * 64)

    result = DropRouter(budget=DropBudget(max_probe_bytes=8)).resolve((source,))

    assert result.status is DropResolutionStatus.CANDIDATE
    assert result.items[0].kind is DropKind.PLUGIN


def test_symlink_and_unreadable_inputs_have_stable_diagnostics(tmp_path: Path, monkeypatch) -> None:
    source = _write(tmp_path / "Demo.esp", b"TES4" + b"\0" * 12)
    link = tmp_path / "Demo-link.esp"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("symlink creation is not available")

    linked = DropRouter().resolve((link,))
    assert linked.diagnostics[0].code == "DROP_PATH_LINK"

    original_open = Path.open

    def denied_open(path: Path, *args, **kwargs):
        if path == source.resolve():
            raise PermissionError("denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied_open)
    unreadable = DropRouter().resolve((source,))

    assert unreadable.status is DropResolutionStatus.REJECTED
    assert unreadable.diagnostics[0].code == "DROP_PATH_UNREADABLE"


def test_archive_policy_budget_and_link_rejections_are_preserved(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        info = zipfile.ZipInfo("fomod/ModuleConfig.xml")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "target")

    result = DropRouter(
        budget=DropBudget(
            archive=ArchiveBudget(
                max_entries=1,
                max_total_uncompressed=16,
                max_single_file=16,
                max_compression_ratio=10,
                max_depth=4,
                timeout_seconds=1,
            )
        )
    ).resolve((archive_path,))

    assert result.status is DropResolutionStatus.REJECTED
    assert result.diagnostics[0].code == "DROP_ARCHIVE_MEMBER_LINK"


def test_archive_member_count_budget_is_checked_before_proposing_fomod(tmp_path: Path) -> None:
    archive_path = tmp_path / "too-many.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("fomod/ModuleConfig.xml", "<config/>")
        archive.writestr("readme.txt", "hello")

    result = DropRouter(
        budget=DropBudget(
            archive=ArchiveBudget(
                max_entries=1,
                max_total_uncompressed=1024,
                max_single_file=1024,
                max_compression_ratio=100,
                max_depth=4,
                timeout_seconds=1,
            )
        )
    ).resolve((archive_path,))

    assert result.status is DropResolutionStatus.REJECTED
    assert result.candidate is None
    assert result.diagnostics[0].code == "DROP_ARCHIVE_COUNT_LIMIT"


def test_supported_7z_candidate_can_use_shared_read_only_archive_boundary(tmp_path: Path) -> None:
    archive_path = _write(tmp_path / "Demo.7z", b"fixture metadata is injected")
    manifest = ArchiveManifest(
        "7z",
        (ArchiveMember("fomod/ModuleConfig.xml", 10, 5, ArchiveMemberType.FILE),),
        10,
        5,
    )
    calls: list[str] = []

    def inspect(path: str, _policy) -> ArchiveManifest:
        calls.append(path)
        return manifest

    result = DropRouter(archive_inspector=inspect).resolve((archive_path,))

    assert result.status is DropResolutionStatus.CANDIDATE
    assert result.candidate is not None and result.candidate.intent_id is IntentId.PUBLISH_FOMOD
    assert calls == [str(archive_path.resolve())]
