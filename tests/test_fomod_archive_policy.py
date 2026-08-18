"""FOMOD S01: shared archive policy, staged extraction, and root discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import stat
import struct
import threading
import uuid
import zipfile
import zlib

import pytest

from transbridge.fileops import (
    ArchiveBudget,
    ArchiveCancelledError,
    ArchiveExtractionError,
    ArchiveExtractor,
    ArchiveMember,
    ArchiveMemberType,
    ArchivePolicy,
    ArchivePolicyError,
    extract,
    inspect_archive,
)
from transbridge.fomod.discovery import detect_mod_roots, extract_fomod_archive


@pytest.fixture
def workdir():
    base = Path(__file__).resolve().parent.parent / ".tmp_tests"
    base.mkdir(exist_ok=True)
    directory = base / f"fomod_archive_{uuid.uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def _zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def _rar4_stored(path: Path, name: str, content: bytes) -> None:
    """Create a tiny, deterministic RAR4 corpus without an external CLI."""

    def block(block_type: int, flags: int, payload: bytes) -> bytes:
        header = bytes([block_type]) + struct.pack("<HH", flags, 7 + len(payload)) + payload
        return struct.pack("<H", zlib.crc32(header) & 0xFFFF) + header

    encoded_name = name.encode("utf-8")
    main = block(0x73, 0, struct.pack("<HI", 0, 0))
    file_header = (
        struct.pack(
            "<LLBLLBBHL",
            len(content),
            len(content),
            2,
            zlib.crc32(content) & 0xFFFFFFFF,
            0,
            20,
            0x30,
            len(encoded_name),
            0x20,
        )
        + encoded_name
    )
    stored_file = block(0x74, 0x8000, file_header)
    end = block(0x7B, 0, b"")
    path.write_bytes(bytes.fromhex("526172211a0700") + main + stored_file + content + end)


@pytest.mark.parametrize(
    "name",
    ["../escape.txt", "/absolute.txt", "C:/drive.txt", "//server/share.txt", "safe/../../x"],
)
def test_unsafe_zip_member_rejected_before_destination_write(workdir, name):
    archive = workdir / "unsafe.zip"
    _zip(archive, {name: b"bad", "safe.txt": b"safe"})
    destination = workdir / "destination"

    with pytest.raises(ArchivePolicyError) as captured:
        extract(str(archive), str(destination))

    assert captured.value.diagnostic.code == "ARCHIVE_PATH_UNSAFE"
    assert not destination.exists()
    assert not list(workdir.glob(".destination.tb_extract_*"))


def test_zip_symlink_rejected_before_extraction(workdir):
    archive_path = workdir / "link.zip"
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(info, "target")

    with pytest.raises(ArchivePolicyError) as captured:
        extract(str(archive_path), str(workdir / "destination"))

    assert captured.value.diagnostic.code == "ARCHIVE_MEMBER_LINK"
    assert not (workdir / "destination").exists()


@pytest.mark.parametrize(
    ("members", "budget", "code"),
    [
        ({"a": b"1", "b": b"2"}, ArchiveBudget(max_entries=1), "ARCHIVE_COUNT_LIMIT"),
        (
            {"a/b/c.txt": b"x"},
            ArchiveBudget(max_depth=2),
            "ARCHIVE_DEPTH_LIMIT",
        ),
        (
            {"large.txt": b"0" * 4_096},
            ArchiveBudget(max_compression_ratio=2),
            "ARCHIVE_COMPRESSION_RATIO_LIMIT",
        ),
    ],
)
def test_zip_budget_rejection_has_zero_destination_writes(workdir, members, budget, code):
    archive = workdir / "budget.zip"
    _zip(archive, members)

    with pytest.raises(ArchivePolicyError) as captured:
        extract(str(archive), str(workdir / "destination"), budget=budget)

    assert captured.value.diagnostic.code == code
    assert not (workdir / "destination").exists()


def test_unicode_long_path_zip_success_chain(workdir):
    relative = "模组/" + "/".join(f"目录{i}" for i in range(8)) + "/插件.esp"
    archive = workdir / "unicode.zip"
    _zip(archive, {relative: b"plugin", "模组/fomod/ModuleConfig.xml": b"<config/>"})

    result = extract_fomod_archive(archive, workdir / "destination")

    assert (workdir / "destination" / Path(relative)).read_bytes() == b"plugin"
    assert result.extraction.extracted_count == 2
    assert result.roots.selected is not None
    assert result.roots.selected.relative_path == "模组"


def test_cancellation_cleans_staging_and_never_exposes_partial_destination(workdir):
    archive = workdir / "cancel.zip"
    _zip(archive, {"a.txt": b"a", "b.txt": b"b"})
    cancellation = threading.Event()

    def cancel_after_first(completed, total):
        del total
        if completed == 1:
            cancellation.set()

    with pytest.raises(ArchiveCancelledError):
        extract(
            str(archive),
            str(workdir / "destination"),
            progress=cancel_after_first,
            cancellation=cancellation,
        )

    assert not (workdir / "destination").exists()
    assert not list(workdir.glob(".destination.tb_extract_*"))


@dataclass
class _LyingInspector:
    def list_members(self, archive_path):
        del archive_path
        return (
            "zip",
            (ArchiveMember("data.bin", 1, 1, ArchiveMemberType.FILE),),
        )


def test_actual_size_fault_is_fail_closed_and_cleans_staging(workdir):
    archive = workdir / "lying.zip"
    _zip(archive, {"data.bin": b"actual-is-longer"})
    extractor = ArchiveExtractor(inspector=_LyingInspector())

    with pytest.raises(ArchiveExtractionError) as captured:
        extractor.extract(archive, workdir / "destination")

    assert captured.value.code == "ARCHIVE_SIZE_MISMATCH"
    assert not (workdir / "destination").exists()


class _Clock:
    def __init__(self):
        self.values = iter((0.0, 0.0, 2.0))

    def __call__(self):
        return next(self.values)


def test_inspection_timeout_prevents_extraction(workdir):
    archive = workdir / "timeout.zip"
    _zip(archive, {"data.bin": b"x"})
    extractor = ArchiveExtractor(
        policy=ArchivePolicy(ArchiveBudget(timeout_seconds=1)),
        clock=_Clock(),
    )

    with pytest.raises(ArchiveExtractionError) as captured:
        extractor.extract(archive, workdir / "destination")

    assert captured.value.code == "ARCHIVE_TIMEOUT"
    assert not (workdir / "destination").exists()


def test_multiple_roots_require_confirmation_and_zero_roots_are_explicit(workdir):
    extracted = workdir / "many"
    for name in ("ModA", "ModB"):
        root = extracted / name
        root.mkdir(parents=True)
        (root / f"{name}.esp").write_bytes(b"plugin")

    many = detect_mod_roots(extracted)
    assert many.selected is None
    assert many.confirmation_required is True
    assert {candidate.relative_path for candidate in many.candidates} == {"ModA", "ModB"}

    empty = workdir / "empty"
    empty.mkdir()
    none = detect_mod_roots(empty)
    assert none.candidates == ()
    assert none.selected is None
    assert none.confirmation_required is False


def test_7z_uses_same_policy_and_extracts_without_extractall(workdir, monkeypatch):
    py7zr = pytest.importorskip("py7zr")
    source = workdir / "source"
    source.mkdir()
    (source / "插件.esp").write_bytes(b"plugin")
    archive_path = workdir / "sample.7z"
    with py7zr.SevenZipFile(archive_path, "w") as archive:
        archive.writeall(source, arcname="Mod")

    def forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("extractall is forbidden")

    monkeypatch.setattr(py7zr.SevenZipFile, "extractall", forbidden)
    manifest = inspect_archive(archive_path)
    result = extract(str(archive_path), str(workdir / "destination"))

    assert manifest.archive_format == "7z"
    assert result["extracted_count"] == 1
    assert (workdir / "destination" / "Mod" / "插件.esp").read_bytes() == b"plugin"


@pytest.mark.parametrize("archive_format", ["zip", "7z", "rar"])
def test_policy_is_backend_neutral(archive_format):
    policy = ArchivePolicy(ArchiveBudget(max_total_uncompressed=10))
    members = (ArchiveMember("safe/file.txt", 11, 5, ArchiveMemberType.FILE),)

    with pytest.raises(ArchivePolicyError) as captured:
        policy.evaluate(members, archive_format=archive_format)

    assert captured.value.diagnostic.code == "ARCHIVE_TOTAL_SIZE_LIMIT"


def test_rar_missing_backend_is_explicit_and_writes_nothing(workdir, monkeypatch):
    import rarfile

    from transbridge.fileops import archive as archive_module

    monkeypatch.setattr(
        archive_module,
        "_find_unrar",
        lambda: (_ for _ in ()).throw(archive_module.ArchiveCapabilityError("ARCHIVE_RAR_BACKEND_UNAVAILABLE")),
    )

    class RarInspector:
        def list_members(self, archive_path):
            del archive_path
            return "rar", (ArchiveMember("safe.txt", 1, 1, ArchiveMemberType.FILE),)

    class FakeInfo:
        filename = "safe.txt"

    class FakeRarFile:
        def __init__(self, archive_path):
            del archive_path

        def __enter__(self):
            return self

        def __exit__(self, *args):
            del args

        @staticmethod
        def infolist():
            return [FakeInfo()]

        @staticmethod
        def open(info, mode):
            del info, mode
            raise rarfile.RarCannotExec("controlled missing backend")

    monkeypatch.setattr(rarfile, "RarFile", FakeRarFile)
    extractor = ArchiveExtractor(inspector=RarInspector())
    with pytest.raises(archive_module.ArchiveCapabilityError):
        extractor.extract(workdir / "fake.rar", workdir / "destination")
    assert not (workdir / "destination").exists()


def test_stored_rar_real_corpus_uses_same_staged_boundary(workdir):
    payload = b"rar-payload"
    archive = workdir / "stored.rar"
    _rar4_stored(archive, "Mod/plugin.esp", payload)
    result = ArchiveExtractor().extract(archive, workdir / "destination")

    assert result.extracted_count == 1
    assert (workdir / "destination" / "Mod" / "plugin.esp").read_bytes() == payload


def test_commit_permission_failure_cleans_staging_and_preserves_empty_target(workdir, monkeypatch):
    from transbridge.fileops import archive as archive_module

    archive = workdir / "permission.zip"
    _zip(archive, {"safe.txt": b"safe"})
    destination = workdir / "destination"
    destination.mkdir()

    def denied(source, target):
        del source, target
        raise PermissionError("controlled")

    monkeypatch.setattr(archive_module.os, "replace", denied)
    with pytest.raises(PermissionError):
        extract(str(archive), str(destination))

    assert destination.is_dir()
    assert not tuple(destination.iterdir())
    assert not list(workdir.glob(".destination.tb_extract_*"))


def test_windows_transient_commit_permission_failure_is_retried(workdir, monkeypatch):
    from transbridge.fileops import archive as archive_module

    archive = workdir / "transient-permission.zip"
    _zip(archive, {"safe.txt": b"safe"})
    destination = workdir / "destination"
    real_replace = archive_module.os.replace
    attempts = {"count": 0}

    def transient(source, target):
        attempts["count"] += 1
        if attempts["count"] < 3:
            error = PermissionError("controlled transient")
            error.winerror = 5
            raise error
        real_replace(source, target)

    monkeypatch.setattr(archive_module.os, "name", "nt")
    monkeypatch.setattr(archive_module.os, "replace", transient)
    result = extract(str(archive), str(destination))

    assert result["extracted_count"] == 1
    assert attempts["count"] == 3
    assert (destination / "safe.txt").read_bytes() == b"safe"


def test_selective_extraction_still_rejects_unselected_unsafe_member(workdir):
    archive = workdir / "selective.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("safe.txt", b"safe")
        output.writestr("../escape.txt", b"bad")

    with pytest.raises(ArchivePolicyError):
        extract(str(archive), str(workdir / "destination"), files=["safe.txt"])

    assert not (workdir / "destination").exists()
