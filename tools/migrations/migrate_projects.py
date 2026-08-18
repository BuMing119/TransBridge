"""One-time conversion of existing TransBridge projects into the current model."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.io.identity import EntryRevision
from transbridge.application.io.stage_policy import Stage
from transbridge.persistence.v2 import (
    OsPersistenceFilesystem,
    ProjectDto,
    ProjectId,
    ProjectRef,
    ProjectRepository,
    SchemaEnvelope,
    SourceFingerprint,
    VariantDto,
    VariantEntryState,
    VariantId,
    VariantRef,
    VariantRepository,
    VariantSnapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    data_dir = args.data_dir.resolve(strict=True)
    workspace_path = data_dir / "workspace.json"
    workspace = _read_object(workspace_path)
    projects = workspace.get("projects")
    if not isinstance(projects, dict) or not projects:
        raise ValueError("workspace.json contains no projects")

    converted: list[dict[str, Any]] = []
    for display_name, raw_path in projects.items():
        converted.append(_convert_project(data_dir, Path(str(raw_path)), args.apply))
        print(f"validated {display_name}: {converted[-1]['entry_count']} entries")

    active_name = workspace.get("active_project")
    active = next((item for item in converted if item["name"] == active_name), converted[0])
    current_workspace = {
        "schema_version": 2,
        "active_project_id": active["project_id"],
        "active_variant_id": active["variant_id"],
        "projects": [
            {
                "name": item["name"],
                "project_id": item["project_id"],
                "active_variant_id": item["variant_id"],
            }
            for item in converted
        ],
    }
    if args.apply:
        _backup(data_dir, workspace_path, "workspace")
        _atomic_json(data_dir / "current-workspace.json", current_workspace)
        _atomic_json(
            data_dir / "active-project.json",
            {
                "schema_version": 1,
                "project_id": active["project_id"],
                "variant_id": active["variant_id"],
                "source_ref": None,
            },
        )
        print(f"activated {active['name']}")
    else:
        print("dry run complete; no files were written")
    return 0


def _convert_project(data_dir: Path, project_path: Path, apply: bool) -> dict[str, Any]:
    project_path = project_path.resolve(strict=True)
    project = _read_object(project_path)
    name = _text(project, "name")
    variants = project.get("variants")
    if not isinstance(variants, list) or len(variants) != 1:
        raise ValueError(f"{name}: one-time converter requires exactly one variant")
    variant_name = _text(variants[0], "name")
    current_path = (project_path.parent / variant_name / "current.json").resolve(strict=True)
    current = _read_object(current_path)
    project_id = ProjectId(f"project-{hashlib.sha256(project_path.read_bytes()).hexdigest()[:24]}")
    variant_seed = f"{project_id.value}\0{variant_name}".encode()
    variant_id = VariantId(f"variant-{hashlib.sha256(variant_seed).hexdigest()[:24]}")
    project_ref = ProjectRef(project_id)
    variant_ref = VariantRef(variant_id, project_id)

    fingerprints: list[SourceFingerprint] = []
    baseline_entries: list[VariantEntryState] = []
    local_key_count: dict[str, int] = {}
    for source in project.get("sources", ()):
        source_path = Path(_text(source, "path")).resolve(strict=True)
        parsed = TranslationIoUseCase().parse(
            ParseRequest(
                SourceDescriptor(str(source_path), source_path.name, source_path.stat().st_size),
                RequestContext("project-converter", run_id=f"convert-{project_id.value}"),
                format_hint=FormatId.PLUGIN_SSE,
                options=(("skip_empty", False),),
            )
        )
        allowed_partial = parsed.outcome is OperationOutcome.PARTIAL and all(
            item.code == "SOURCE_LOCATOR_CONFLICT" for item in parsed.diagnostics
        )
        if (parsed.outcome is not OperationOutcome.COMPLETED and not allowed_partial) or parsed.source_snapshot is None:
            code = parsed.diagnostics[0].code if parsed.diagnostics else "SOURCE_PARSE_FAILED"
            raise ValueError(f"{name}: {code}")
        if allowed_partial:
            print(f"warning {name}: excluded {parsed.stats.failed} source records with duplicate write locators")
        namespace = parsed.entries[0].identity.namespace
        fingerprints.append(SourceFingerprint(namespace, parsed.source_snapshot.sha256))
        for entry in parsed.entries:
            key = entry.identity.local_key
            local_key_count[key] = local_key_count.get(key, 0) + 1
            baseline_entries.append(
                VariantEntryState(
                    entry.identity,
                    entry.translation,
                    entry.stage,
                    provenance=entry.provenance,
                    revision=entry.revision,
                )
            )

    translations = current.get("translations") or {}
    labels = current.get("labels") or {}
    entry_states = current.get("entry_states") or {}
    unknown = sorted((set(translations) | set(labels) | set(entry_states)) - set(local_key_count))
    ambiguous = sorted(
        key for key in set(translations) | set(labels) | set(entry_states) if local_key_count.get(key, 0) > 1
    )
    if unknown:
        print(f"warning {name}: preserved {len(unknown)} unresolved saved entry keys")
    if ambiguous:
        raise ValueError(f"{name}: {len(ambiguous)} saved entry keys are ambiguous")

    entries: list[VariantEntryState] = []
    for entry in baseline_entries:
        key = entry.entry_key.local_key
        translation = str(translations.get(key, entry.translation))
        saved_state = entry_states.get(key) or {}
        stage = Stage.from_value(saved_state.get("stage", Stage.TRANSLATED.value if translation else entry.stage))
        if stage is None:
            raise ValueError(f"{name}: invalid stage for {key}")
        revision = EntryRevision(saved_state.get("revision", entry.revision.value))
        entries.append(
            VariantEntryState(
                entry.entry_key,
                translation,
                stage,
                tuple(str(value) for value in labels.get(key, ())),
                entry.provenance,
                revision,
                inferred_fields=("provenance",) if not entry.provenance else (),
            )
        )

    project_dto = ProjectDto(
        SchemaEnvelope(
            2,
            project_ref.kind,
            project_id.value,
            0,
            {
                "name": name,
                "sources": project.get("sources", []),
                "variant_ids": [variant_id.value],
                "active_variant_id": variant_id.value,
            },
        )
    )
    snapshot = VariantSnapshot(
        variant_ref,
        tuple(fingerprints),
        tuple(entries),
        label_library=tuple((str(key), value) for key, value in (current.get("label_library") or {}).items()),
    )
    variant_dto = snapshot.to_dto()
    if unknown:
        variant_data = dict(variant_dto.envelope.data)
        variant_data["migration_unresolved"] = {
            key: {
                "translation": translations.get(key),
                "labels": labels.get(key),
                "entry_state": entry_states.get(key),
                "reason": "source locator is absent or ambiguous",
            }
            for key in unknown
        }
        variant_dto = VariantDto(
            SchemaEnvelope(
                variant_dto.envelope.schema_version,
                variant_dto.envelope.entity_type,
                variant_dto.envelope.identity,
                variant_dto.envelope.revision,
                variant_data,
            )
        )
    if apply:
        _backup(data_dir, project_path, f"{project_id.value}-project")
        _backup(data_dir, current_path, f"{variant_id.value}-variant")
        filesystem = OsPersistenceFilesystem()
        variants_repo = VariantRepository(str(data_dir), filesystem)
        projects_repo = ProjectRepository(str(data_dir), filesystem)
        if Path(variants_repo.path_for(variant_ref)).exists() or Path(projects_repo.path_for(project_ref)).exists():
            raise ValueError(f"{name}: converted records already exist; overwrite refused")
        variants_repo.save(variant_ref, variant_dto)
        projects_repo.save(project_ref, project_dto)
    return {
        "name": name,
        "project_id": project_id.value,
        "variant_id": variant_id.value,
        "entry_count": len(entries),
        "unresolved_count": len(unknown),
    }


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return value


def _text(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return result


def _backup(data_dir: Path, source: Path, label: str) -> Path:
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    destination = data_dir / "backups" / "project-import" / f"{label}-{digest}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copyfile(source, destination)
    if destination.read_bytes() != raw:
        raise ValueError(f"backup verification failed: {source}")
    return destination


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    stage = path.with_suffix(".tmp")
    stage.unlink(missing_ok=True)
    with stage.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(stage, path)


if __name__ == "__main__":
    sys.exit(main())
