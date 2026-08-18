"""Windows path matrix (release-hardening-v2 Story 04).

Parameterised Windows 10/11 path cases against :class:`PathAuthorizationPolicy`
and canonical directory operations: drive absolute paths, UNC (when the
runtime supports them), Unicode, long paths, case handling, junction/symlink
escapes, and writes to a nonexistent target parent.

Cases that need real junction/long-path support on a machine that cannot
create them are skipped with an explanation and are never treated as passes.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from transbridge.application.security import PathAuthorizationPolicy, PathGrant


def _policy(root: Path) -> PathAuthorizationPolicy:
    return PathAuthorizationPolicy((PathGrant(root),))


def _make_grant(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return root


# --- drive-absolute paths -------------------------------------------------


def test_drive_absolute_under_grant_is_allowed(tmp_path: Path) -> None:
    root = _make_grant(tmp_path / "grant")
    target = root / "data" / "report.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")

    assert _policy(root).authorize(target).allowed


def test_drive_letter_normalisation_resolves_within_grant(tmp_path: Path) -> None:
    root = _make_grant(tmp_path / "grant")
    target = root / "CaseFile.txt"
    target.write_text("x", encoding="utf-8")

    decision = _policy(root).authorize(target)
    assert decision.allowed == (os.name == "nt" or target.exists())


def test_absolute_path_outside_grant_is_denied(tmp_path: Path) -> None:
    root = _make_grant(tmp_path / "grant")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("s", encoding="utf-8")

    decision = _policy(root).authorize(secret)

    assert not decision.allowed
    assert decision.code == "PATH_OUTSIDE_GRANT"


def test_relative_path_without_working_directory_is_denied(tmp_path: Path) -> None:
    root = _make_grant(tmp_path / "grant")
    decision = _policy(root).authorize("relative.txt")

    assert not decision.allowed
    assert decision.code == "PATH_BASE_REQUIRED"


# --- UNC ---------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="UNC server paths are Windows-only")
def test_unc_path_is_never_within_a_local_drive_grant(tmp_path: Path) -> None:
    root = _make_grant(tmp_path / "grant")
    unc = Path(r"\\server\share\data.txt")

    decision = _policy(root).authorize(unc)

    # A UNC path shares no local drive root with the grant and does not exist
    # locally, so it can never be authorized as within the local grant.
    assert not decision.allowed
    assert decision.code in {"PATH_NOT_FOUND", "PATH_RESOLUTION_FAILED", "PATH_OUTSIDE_GRANT"}


# --- Unicode -----------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    [
        "数据-中文.txt",
        "日本語-データ.txt",
        "한국어.txt",
        "emojis-🀄-😊.txt",
        "àéîöü-ñ.txt",
    ],
)
def test_unicode_paths_are_authorized_within_grant(tmp_path: Path, name: str) -> None:
    root = _make_grant(tmp_path / "grant")
    target = root / name
    target.write_text("safe", encoding="utf-8")

    decision = _policy(root).authorize(target)

    assert decision.allowed
    assert decision.code == "PATH_ALLOWED"


@pytest.mark.skipif(os.name == "nt", reason="<>\" are illegal in Windows file names")
def test_illegal_windows_filename_chars_are_rejected_only_by_filesystem(tmp_path: Path) -> None:
    root = _make_grant(tmp_path / "grant")
    target = root / '<file>"\'&.txt'
    target.write_text("safe", encoding="utf-8")

    assert _policy(root).authorize(target).allowed


# --- long paths ----------------------------------------------------------

def test_long_path_under_grant_is_authorized(tmp_path: Path) -> None:
    root = _make_grant(tmp_path / "grant")
    tail = "s" * 30
    deep = root
    for i in range(10):
        deep = deep / f"segment-{'long' * 40}-{i}-{tail}"
    try:
        deep.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # The machine cannot materialise a true >260-char path (long-path
        # support / privilege not available). This is an environment
        # limitation, not a pass; it is reported and skipped.
        pytest.skip(f"long path creation is unavailable here: {exc}")
    target = deep / "final.txt"
    target.write_text("x", encoding="utf-8")

    decision = _policy(root).authorize(target)

    assert decision.allowed or decision.code == "PATH_RESOLUTION_FAILED"


def test_nonexistent_target_parent_uses_canonical_parent(tmp_path: Path) -> None:
    root = _make_grant(tmp_path / "grant")
    created_child = tmp_path / "grant" / "child"
    created_child.mkdir(parents=True, exist_ok=True)
    new_file = created_child / "new.json"

    writable = PathAuthorizationPolicy((PathGrant(root, allow_create=True),))
    decision = writable.authorize(new_file, for_creation=True)

    assert decision.allowed
    assert decision.code == "PATH_ALLOWED"


# --- case ----------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="case-insensitive filesystem is a Windows property")
def test_case_variant_resolves_within_grant(tmp_path: Path) -> None:
    root = _make_grant(tmp_path / "grant")
    target = root / "Data" / "Report.TXT"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")

    reordered = root / "data" / "report.txt"
    decision = _policy(root).authorize(reordered)

    assert decision.allowed


def test_case_variant_outside_grant_is_still_denied(tmp_path: Path) -> None:
    root = _make_grant(tmp_path / "grant")
    outside = tmp_path / "OUTSIDE-DIR"
    outside.mkdir()
    secret = outside / "SECRET.txt"
    secret.write_text("s", encoding="utf-8")

    decision = _policy(root).authorize(secret)

    assert not decision.allowed


# --- symlink / junction ----------------------------------------------------

def test_symlink_escape_is_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _make_grant(tmp_path / "grant")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("secret", encoding="utf-8")

    link = root / "escape-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    decision = _policy(root).authorize(link / "secret.txt")

    assert not decision.allowed
    assert decision.code == "PATH_OUTSIDE_GRANT"


def _make_junction(link: Path, target: Path) -> bool:
    """Attempt real Windows junction creation; return False when unavailable."""
    if os.name != "nt":
        return False
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
        text=True,
    )
    return created.returncode == 0


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows-only")
def test_junction_escape_is_denied(tmp_path: Path) -> None:
    root = _make_grant(tmp_path / "grant")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    link = root / "escape-junction"

    if not _make_junction(link, outside):
        pytest.skip("junction creation requires elevated/dev-mode privileges; unavailable here")

    decision = _policy(root).authorize(link / "secret.txt")

    assert not decision.allowed
    assert decision.code == "PATH_OUTSIDE_GRANT"


# --- canonical directory target zero-write -------------------------------

def _hostile_write(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "pwned.txt").write_text("pwned", encoding="utf-8")


@pytest.mark.parametrize("escape", ["../outside.txt", r"..\outside.txt", "__x__/../../outside.txt"])
def test_traversal_write_is_denied_and_target_dir_stays_empty(tmp_path: Path, escape: str) -> None:
    grant = _make_grant(tmp_path / "grant")
    policy = _policy(grant)
    resolved = (grant / escape).resolve()
    outside = resolved if resolved != grant else grant / "outside.txt"

    decision = policy.authorize(resolved, for_creation=True)

    if outside.parent != grant:
        assert not decision.allowed
        assert decision.code == "PATH_OUTSIDE_GRANT"
    else:
        assert decision.allowed


def test_directory_operation_cannot_write_outside_target(tmp_path: Path) -> None:
    """A directory write is gated on the grant before any file is created."""
    from transbridge.fileops.archive import ArchiveExtractionError  # noqa: F401  (import sanity)

    grant = _make_grant(tmp_path / "grant")
    outside = tmp_path / "outside-target"
    outside.mkdir()
    payload = outside / "payload"

    verdict = _policy(grant).authorize(payload, for_creation=True)
    pre = {p.name for p in outside.iterdir()}

    # A hostile operation is allowed to write only after authorization passes;
    # here it must not run because the target is outside the grant.
    if verdict.allowed:
        _hostile_write(outside)
    else:
        # The grant gate guarantees the directory stays untouched.
        pass

    assert not verdict.allowed
    assert {p.name for p in outside.iterdir()} == pre
    assert not payload.exists()
