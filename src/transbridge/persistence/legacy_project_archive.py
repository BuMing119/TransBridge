"""Prepare legacy project bundles without publishing or mutating source files."""

from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any
from zipfile import ZipFile

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryKey, Provenance
from transbridge.application.preflight.sources import source_reference

from .legacy_project_records import LegacyArchiveRecords, LegacyProjectRecords
from .legacy_project_tasks import is_legacy_task_member
from .v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from .v2.migration import migrate_to_current
from .v2.models import ProjectDto, SchemaValidationError, VariantDto
from .v2.schema import parse_json_bytes, validate_v2, version_of
from .v2.variant import VariantSnapshot


def decode_legacy_archive(
    archive: ZipFile,
    *,
    project_id: ProjectId,
    source_preparer,
    context: RequestContext,
) -> tuple[ProjectDto, tuple[VariantSnapshot, ...], tuple[dict, ...]]:
    """Decode a path/size-validated ZIP through the shared migration boundary."""

    return decode_legacy_records(
        LegacyArchiveRecords(archive), project_id=project_id, source_preparer=source_preparer, context=context
    )


def decode_legacy_records(
    archive: LegacyProjectRecords,
    *,
    project_id: ProjectId,
    source_preparer,
    context: RequestContext,
) -> tuple[ProjectDto, tuple[VariantSnapshot, ...], tuple[dict, ...]]:
    """Decode validated legacy records; missing sources retain unverified state.

    Legacy packages contain JSON records, not captured source content. Saved
    qualified identities and bare IDs are retained without opening references.
    The caller owns atomic publication and read-only recovery activation.
    """

    raw_project = _read_document(archive, "project.json")
    project_source = deepcopy(raw_project)
    _restore_format_hints(project_source)
    project_ref = ProjectRef(project_id)
    document = migrate_to_current(project_source, project_ref).document
    project = validate_v2(document, project_ref)
    if not isinstance(project, ProjectDto):
        raise ValueError("旧项目包缺少有效工程记录")
    project.envelope.data["sources"] = [source_reference(source) for source in project.envelope.data["sources"]]
    mapping = project.envelope.data["legacy"]["variant_name_map"]
    members = archive.members
    expected = {"project.json"}
    for name in mapping:
        if name in {".", ".."} or "/" in name or "\\" in name:
            raise ValueError(f"旧项目包版本目录无效：{name!r}")
        expected.add(f"{name}/current.json")
    snapshots = []
    for member in sorted(members - expected):
        parts = PurePosixPath(member).parts
        if len(parts) == 3 and parts[0] in mapping and parts[1] == "snapshots" and member.endswith(".json"):
            snapshots.append(member)
            continue
        if is_legacy_task_member(member):
            continue
        raise ValueError(f"旧项目包包含无法迁移的记录，未导入以免丢失数据：{member}")
    missing = expected - members
    if missing:
        raise ValueError(f"旧项目包缺少版本记录：{', '.join(sorted(missing))}")

    variants = tuple(
        _decode_variant(archive, f"{name}/current.json", VariantRef(VariantId(value), project_id))
        for name, value in mapping.items()
    )
    documents = []
    used_names: set[tuple[str, str]] = set()
    for member in snapshots:
        ref = VariantRef(VariantId(mapping[PurePosixPath(member).parts[0]]), project_id)
        raw = _read_document(archive, member)
        snapshot = _decode_variant(archive, member, ref, raw=raw)
        name = raw.get("snapshot_name", PurePosixPath(member).stem)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"旧项目包快照名称无效：{member}")
        # Legacy snapshots can repeat a display name with revision zero. V2
        # identifies snapshots by name and revision, so keep every saved copy.
        if (ref.identity.value, name) in used_names:
            name = f"{name} ({PurePosixPath(member).stem})"
        while (ref.identity.value, name) in used_names:
            name += " (副本)"
        used_names.add((ref.identity.value, name))
        documents.append({
            "schema_version": 1,
            "name": name,
            "project_id": project_id.value,
            "variant": snapshot.to_dto().envelope.to_dict(),
        })
    project.envelope.data["legacy"]["archive_project"] = raw_project
    if any(variant.entries for variant in variants) or any(item["variant"]["data"]["entries"] for item in documents):
        project.envelope.data["legacy"]["archive_recovery"] = "source-baseline-required"
    validate_v2(project.envelope.to_dict(), project_ref)
    return project, variants, tuple(documents)


def _read_document(archive: LegacyProjectRecords, member: str) -> dict[str, Any]:
    try:
        document = parse_json_bytes(archive.read(member))
    except (KeyError, ValueError, SchemaValidationError) as exc:
        raise ValueError(f"无法读取旧项目包记录：{member}；{exc}") from exc
    if version_of(document) != 1:
        raise ValueError(f"旧项目包记录不是 V1 格式：{member}")
    return document


def _restore_format_hints(document: dict[str, Any]) -> None:
    """Retain the legacy parser selection before source registry migration."""

    sources = document.get("sources", [])
    if not isinstance(sources, list):
        return
    for source in sources:
        if not isinstance(source, dict) or source.get("format_id"):
            continue
        kind = source.get("type")
        location = str(source.get("location") or source.get("path") or "")
        if kind == "xt":
            source["format_id"] = FormatId.XML_XT.value
        elif kind == "eet":
            source["format_id"] = (FormatId.BINARY_EET if location.lower().endswith(".eet") else FormatId.XML_EET).value
        elif kind == "sst":
            source["format_id"] = FormatId.SST_SSU8.value


def _decode_variant(
    archive: LegacyProjectRecords,
    member: str,
    ref: VariantRef,
    *,
    raw: dict[str, Any] | None = None,
) -> VariantSnapshot:
    source = raw if raw is not None else _read_document(archive, member)
    supported = {
        "schema_version",
        "id",
        "variant_id",
        "variant",
        "updated",
        "translations",
        "labels",
        "label_library",
        "revision",
        "entry_states",
        "snapshot_name",
    }
    if set(source) - supported:
        raise ValueError(f"旧项目包版本包含无法迁移的字段：{member}，{', '.join(sorted(set(source) - supported))}")
    document = migrate_to_current(source, ref).document
    data = document["data"]
    states = source.get("entry_states", {})
    if not isinstance(states, dict) or any(
        not isinstance(key, str) or not isinstance(value, dict) for key, value in states.items()
    ):
        raise ValueError(f"旧项目包 entry_states 必须是条目 ID 到状态对象的映射：{member}")
    rows = {entry["entry_key"]["local_key"]: entry for entry in data["entries"]}
    for key, state in states.items():
        if not key or set(state) - {"stage", "revision", "provenance"}:
            raise ValueError(f"旧项目包条目状态包含无法迁移的字段：{member}，{key!r}")
        row = rows.setdefault(
            key,
            {
                "entry_key": {"namespace": "legacy:v1", "local_key": key},
                "translation": "",
                "stage": 0,
                "labels": [],
                "provenance": [],
                "revision": 0,
                "tombstone": False,
                "inferred_fields": ["stage", "provenance", "revision"],
            },
        )
        _validate_entry_state(state, member, key)
        row.update(deepcopy(state))
        row["inferred_fields"] = [field for field in row["inferred_fields"] if field not in state]
    for key, row in rows.items():
        qualified = _qualified_cache_key(key)
        if qualified is not None:
            row["entry_key"] = qualified.to_dict()
    # A reference cannot prove the saved content namespace or baseline.
    data["source_fingerprints"] = [
        {"namespace": namespace, "sha256": None}
        for namespace in sorted({row["entry_key"]["namespace"] for row in rows.values()})
    ]
    data["entries"] = list(rows.values())
    validated = validate_v2(document, ref)
    if not isinstance(validated, VariantDto):
        raise ValueError(f"旧项目包版本记录无效：{member}")
    snapshot = VariantSnapshot.from_dto(validated, ref)
    validate_v2(snapshot.to_dto().envelope.to_dict(), ref)
    return snapshot


def _qualified_cache_key(value: str) -> EntryKey | None:
    """Recognize the canonical three-part keys emitted by VariantStore."""
    try:
        key = EntryKey.deserialize(value)
    except ValueError:
        return None
    return key if key.original is not None and key.serialize() == value else None


def _validate_entry_state(state: dict[str, Any], member: str, key: str) -> None:
    if "stage" in state and (isinstance(state["stage"], bool) or not isinstance(state["stage"], int)):
        raise ValueError(f"旧项目包阶段必须是整数：{member}，{key!r}")
    if "provenance" not in state:
        return
    values = state["provenance"]
    if not isinstance(values, list) or not all(isinstance(value, dict) for value in values):
        raise ValueError(f"旧项目包变更来源必须是对象数组：{member}，{key!r}")
    for value in values:
        if set(value) - {"run_id", "actor", "source", "recorded_at", "metadata"}:
            raise ValueError(f"旧项目包变更来源包含无法迁移的字段：{member}，{key!r}")
        if any(
            not isinstance(value.get(field), str) or not value[field].strip() for field in ("run_id", "actor", "source")
        ):
            raise ValueError(f"旧项目包变更来源缺少有效身份：{member}，{key!r}")
        if (
            not isinstance(value.get("metadata", {}), dict)
            or value.get("recorded_at") is not None
            and not isinstance(value["recorded_at"], str)
        ):
            raise ValueError(f"旧项目包变更来源格式无效：{member}，{key!r}")
        Provenance.from_dict(value)


__all__ = ["decode_legacy_archive", "decode_legacy_records"]
