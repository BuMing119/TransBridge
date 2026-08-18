"""Explicit, recoverable migration from legacy TM JSON to schema v2."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity
from transbridge.application.io.identity import Provenance, SourceNamespace


@dataclass(frozen=True, slots=True)
class TranslationMemoryMigrationReport:
    source: str
    backup: str | None
    migrated: int
    disabled: int
    diagnostics: tuple[Diagnostic, ...] = ()


def migrate_legacy_dictionary(
    path: str | Path,
    *,
    run_id: str,
    source_locale: str | None = None,
    target_locale: str | None = None,
    source_namespace: SourceNamespace | None = None,
    source_fingerprint: str = "",
) -> TranslationMemoryMigrationReport:
    """Back up then atomically replace a V1 dictionary; never infer locales."""
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("legacy dictionary root must be an object")
    version = payload.get("schema_version", 1)
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("dictionary schema version must be an integer")
    if version > 2:
        raise ValueError(f"unsupported dictionary schema version: {version}")
    if version == 2:
        return TranslationMemoryMigrationReport(str(source), None, 0, 0)

    backup = _next_backup(source)
    shutil.copy2(source, backup)
    _fsync_file(backup)

    entries = payload.get("entries") or {}
    if not isinstance(entries, dict):
        raise TypeError("legacy dictionary entries must be an object")
    dictionary_id = str(payload.get("dictionary_id") or payload.get("mod_file_id") or source.stem)
    revision = payload.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("legacy dictionary revision must be a non-negative integer")
    next_revision = revision + 1
    locale_known = bool(source_locale and target_locale)
    identity_known = source_namespace is not None and bool(source_fingerprint)
    auto_enabled = locale_known and identity_known
    namespace = (source_namespace or SourceNamespace.legacy()).value
    migrated = 0
    disabled = 0
    for entry_id, raw in entries.items():
        if not isinstance(raw, dict):
            raise TypeError(f"legacy dictionary entry must be an object: {entry_id}")
        raw["source_locale"] = source_locale or ""
        raw["target_locale"] = target_locale or ""
        raw["stage"] = raw.get("stage", 0)
        raw["dictionary_id"] = dictionary_id
        raw["dictionary_revision"] = next_revision
        raw["source_namespace"] = namespace
        raw["source_fingerprint"] = source_fingerprint
        raw["enabled"] = auto_enabled
        provenance = list(raw.get("provenance") or [])
        provenance.append(
            Provenance(
                run_id,
                "tm-schema-migrator",
                "legacy-tm-migration",
                metadata=(("legacy_history", "unknown"),),
            ).to_dict()
        )
        raw["provenance"] = provenance
        migrated += 1
        disabled += int(not auto_enabled)

    payload["schema_version"] = 2
    payload["dictionary_id"] = dictionary_id
    payload["revision"] = next_revision
    _atomic_write_json(source, payload)
    diagnostics = ()
    if disabled and not locale_known:
        diagnostics = (
            Diagnostic(
                "TM_MIGRATION_LOCALE_UNKNOWN",
                "Legacy entries were migrated disabled because their locales could not be proven.",
                DiagnosticSeverity.WARNING,
                details=(("disabled", disabled),),
            ),
        )
    elif disabled:
        diagnostics = (
            Diagnostic(
                "TM_MIGRATION_SOURCE_IDENTITY_UNKNOWN",
                "Legacy entries were migrated disabled because source identity could not be proven.",
                DiagnosticSeverity.WARNING,
                details=(("disabled", disabled),),
            ),
        )
    return TranslationMemoryMigrationReport(
        str(source),
        str(backup),
        migrated,
        disabled,
        diagnostics,
    )


def _next_backup(source: Path) -> Path:
    candidate = source.with_name(f"{source.name}.v1.bak")
    index = 1
    while candidate.exists():
        candidate = source.with_name(f"{source.name}.v1.bak.{index}")
        index += 1
    return candidate


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _fsync_file(path: Path) -> None:
    # Windows requires a writable descriptor for ``fsync``.
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
