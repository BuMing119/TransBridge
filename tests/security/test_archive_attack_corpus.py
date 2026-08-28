"""Malicious archive attack corpus across ZIP/7z/RAR (release-hardening-v2 Story 04).

The same hostile member corpus and budget are asserted to be rejected by
:class:`ArchivePolicy` / :class:`ArchiveExtractor` before any file is written
to the destination.  ZIP is the primary carrier; 7z/RAR are asserted at the
library capability level (reported as ``ARCHIVE_*_BACKEND_UNAVAILABLE`` when
the optional library is absent, or as a clean policy rejection when present).
"""

from __future__ import annotations

from pathlib import Path
import stat
import zipfile

import pytest

from transbridge.fileops.archive import ArchiveCapabilityError, ArchiveExtractor, LibraryArchiveInspector
from transbridge.fileops.archive_policy import (
    ArchiveBudget,
    ArchiveMember,
    ArchiveMemberType,
    ArchivePolicy,
    ArchivePolicyError,
)


def _member(name: str, size: int = 10, compressed: int | None = 5) -> ArchiveMember:
    return ArchiveMember(name, size, compressed, ArchiveMemberType.FILE)


def _symlink_member(name: str) -> ArchiveMember:
    return ArchiveMember(name, 0, 0, ArchiveMemberType.SYMLINK)


def _evaluate(policy: ArchivePolicy, *members: ArchiveMember) -> None:
    policy.evaluate(members, archive_format="zip")


def _diagnostic_code(exc: ArchivePolicyError) -> str:
    return exc.diagnostic.code


# --- shared policy corpus ---------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected_code"),
    [
        ("../escape.txt", "ARCHIVE_PATH_UNSAFE"),
        (r"..\escape.txt", "ARCHIVE_PATH_UNSAFE"),
        ("a/../../escape.txt", "ARCHIVE_PATH_UNSAFE"),
        ("/absolute.txt", "ARCHIVE_PATH_UNSAFE"),
        ("C:/drive.txt", "ARCHIVE_PATH_UNSAFE"),
        (r"C:\drive.txt", "ARCHIVE_PATH_UNSAFE"),
        (r"\\server\share\file.txt", "ARCHIVE_PATH_UNSAFE"),
        ("nul\x00byte", "ARCHIVE_PATH_UNSAFE"),
        ("CON.txt", "ARCHIVE_PATH_UNSAFE"),
        ("a/./b.txt", "ARCHIVE_PATH_UNSAFE"),
    ],
)
def test_traversal_and_absolute_corpus_is_rejected(name: str, expected_code: str) -> None:
    with pytest.raises(ArchivePolicyError) as excinfo:
        _evaluate(ArchivePolicy(), _member(name))
    assert _diagnostic_code(excinfo.value) == expected_code


def test_link_member_is_rejected_before_write() -> None:
    with pytest.raises(ArchivePolicyError) as excinfo:
        _evaluate(ArchivePolicy(), _symlink_member("link"))
    assert _diagnostic_code(excinfo.value) == "ARCHIVE_MEMBER_LINK"


def test_special_member_is_rejected_before_write() -> None:
    special = ArchiveMember("special", 0, 0, ArchiveMemberType.SPECIAL)
    with pytest.raises(ArchivePolicyError) as excinfo:
        _evaluate(ArchivePolicy(), special)
    assert _diagnostic_code(excinfo.value) == "ARCHIVE_MEMBER_SPECIAL"


def test_duplicate_normalised_path_is_rejected() -> None:
    policy = ArchivePolicy()
    with pytest.raises(ArchivePolicyError) as excinfo:
        policy.evaluate([_member("Dir/File.TXT"), _member("dir/file.txt")], archive_format="zip")
    assert _diagnostic_code(excinfo.value) == "ARCHIVE_DUPLICATE_PATH"


# --- budget corpus ----------------------------------------------------------


def test_oversized_single_file_is_rejected() -> None:
    policy = ArchivePolicy(ArchiveBudget(max_single_file=100))
    with pytest.raises(ArchivePolicyError) as excinfo:
        _evaluate(policy, _member("big.bin", size=200, compressed=100))
    assert _diagnostic_code(excinfo.value) == "ARCHIVE_FILE_SIZE_LIMIT"


def test_bomb_compression_ratio_is_rejected() -> None:
    policy = ArchivePolicy(ArchiveBudget(max_compression_ratio=10.0))
    with pytest.raises(ArchivePolicyError) as excinfo:
        _evaluate(policy, _member("bomb.txt", size=10_000, compressed=50))
    assert _diagnostic_code(excinfo.value) == "ARCHIVE_COMPRESSION_RATIO_LIMIT"


def test_expanded_total_size_budget_is_rejected() -> None:
    policy = ArchivePolicy(ArchiveBudget(max_total_uncompressed=1_000))
    with pytest.raises(ArchivePolicyError) as excinfo:
        policy.evaluate(
            [_member(f"f{i}.txt", size=250, compressed=50) for i in range(5)],
            archive_format="zip",
        )
    assert _diagnostic_code(excinfo.value) == "ARCHIVE_TOTAL_SIZE_LIMIT"


def test_member_count_budget_is_rejected() -> None:
    policy = ArchivePolicy(ArchiveBudget(max_entries=3))
    with pytest.raises(ArchivePolicyError) as excinfo:
        policy.evaluate(
            [_member(f"f{i}.txt") for i in range(4)],
            archive_format="zip",
        )
    assert _diagnostic_code(excinfo.value) == "ARCHIVE_COUNT_LIMIT"


def test_member_depth_budget_is_rejected() -> None:
    policy = ArchivePolicy(ArchiveBudget(max_depth=2))
    with pytest.raises(ArchivePolicyError) as excinfo:
        _evaluate(policy, _member("a/b/c/d/e.txt"))
    assert _diagnostic_code(excinfo.value) == "ARCHIVE_DEPTH_LIMIT"


# --- real ZIP attack files: zero-write before extraction ---------------------


def _write_zip(path: Path, entries: list[tuple[str, bytes]]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        for name, data in entries:
            archive.writestr(name, data)


def _write_zip_symlink(path: Path, link_name: str, target: str) -> None:
    info = zipfile.ZipInfo(link_name)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr(info, target)


def test_real_zip_slip_is_rejected_and_dest_untouched(tmp_path: Path) -> None:
    payload = tmp_path / "attack.zip"
    _write_zip(payload, [("../escaped.txt", b"evil")])
    dest = tmp_path / "dest"
    with pytest.raises(ArchivePolicyError) as excinfo:
        ArchiveExtractor().extract(payload, dest)
    assert _diagnostic_code(excinfo.value) == "ARCHIVE_PATH_UNSAFE"
    assert not dest.exists()


def test_real_zip_symlink_is_rejected_and_dest_untouched(tmp_path: Path) -> None:
    payload = tmp_path / "link.zip"
    _write_zip_symlink(payload, "lnk", "/etc/passwd")
    dest = tmp_path / "dest"
    with pytest.raises(ArchivePolicyError) as excinfo:
        ArchiveExtractor().extract(payload, dest)
    assert _diagnostic_code(excinfo.value) == "ARCHIVE_MEMBER_LINK"
    assert not dest.exists()


def test_real_zip_evaluation_happens_before_write(tmp_path: Path) -> None:
    # A corpus mixing one benign member first and a malicious one later must be
    # rejected before *any* member is written to the destination.
    payload = tmp_path / "mixed.zip"
    _write_zip(payload, [("good.txt", b"ok"), ("../bad.txt", b"bad")])
    dest = tmp_path / "dest"
    with pytest.raises(ArchivePolicyError):
        ArchiveExtractor().extract(payload, dest)
    # The evaluator is all-or-nothing: nothing reached disk.
    assert not dest.exists()


# --- 7z / RAR capability reporting ------------------------------------------


def _blocked_import_ctx(monkeypatch: pytest.MonkeyPatch, blocked: str) -> None:
    import builtins
    import sys

    real_import = builtins.__import__
    # Force the module to be re-imported by evicting the cached entry, then
    # intercept the import to surface the missing-dependency capability path.
    monkeypatch.delitem(sys.modules, blocked, raising=False)
    monkeypatch.setitem(sys.modules, blocked, None)

    def blocked_import(name, *args, **kwargs):
        if name == blocked:
            raise ImportError(f"{blocked} unavailable (isolated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)


def test_7z_backend_capability_error_on_missing_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inspector = LibraryArchiveInspector()
    fake_7z = tmp_path / "sample.7z"
    fake_7z.write_bytes(b"7z\xbc\xaf'\x1c")
    _blocked_import_ctx(monkeypatch, "py7zr")

    with pytest.raises(ArchiveCapabilityError) as excinfo:
        inspector.list_members(str(fake_7z))

    assert "ARCHIVE_7Z_BACKEND_UNAVAILABLE" in str(excinfo.value)


def test_rar_backend_capability_error_on_missing_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inspector = LibraryArchiveInspector()
    fake_rar = tmp_path / "sample.rar"
    fake_rar.write_bytes(b"Rar!\x1a\x07\x00")
    _blocked_import_ctx(monkeypatch, "rarfile")

    with pytest.raises(ArchiveCapabilityError) as excinfo:
        inspector.list_members(str(fake_rar))

    assert "ARCHIVE_RAR_BACKEND_UNAVAILABLE" in str(excinfo.value)
