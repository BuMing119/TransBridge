"""Prepare legacy project bundles without publishing or mutating source files."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZipFile

from transbridge.application.contracts import DomainError, RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryKey, Provenance
from transbridge.application.projects import ProjectSourceRequest
from transbridge.application.projects.source_content import authoritative_baseline_sources

from .v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from .v2.migration import migrate_to_current
from .v2.models import ProjectDto, SchemaValidationError, VariantDto
from .v2.schema import parse_json_bytes, validate_v2, version_of
from .v2.variant import SourceFingerprint, VariantEntryState, VariantSnapshot


@dataclass(frozen=True, slots=True)
class _SourceIdentity:
    fingerprints: tuple[SourceFingerprint, ...]
    entries: dict[EntryKey, VariantEntryState]
    legacy_keys: dict[str, frozenset[EntryKey]]
    verified: bool


def decode_legacy_archive(
    archive: ZipFile,
    *,
    project_id: ProjectId,
    source_preparer,
    context: RequestContext,
) -> tuple[ProjectDto, tuple[VariantSnapshot, ...], tuple[dict, ...]]:
    """Decode a path/size-validated ZIP; missing sources retain unverified state.

    Legacy packages contain JSON records, not source files. Only exact, unique
    legacy IDs from a freshly verified source hydration may become EntryKeys.
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
    mapping = project.envelope.data["legacy"]["variant_name_map"]
    members = {item.filename for item in archive.infolist() if not item.is_dir()}
    expected = {"project.json"}
    for name in mapping:
        if name in {".", ".."} or "/" in name or "\\" in name:
            raise ValueError(f"旧项目包版本目录无效：{name!r}")
        expected.add(f"{name}/current.json")
    snapshots = []
    for member in sorted(members - expected):
        parts = PurePosixPath(member).parts
        if len(parts) != 3 or parts[0] not in mapping or parts[1] != "snapshots" or not member.endswith(".json"):
            raise ValueError(f"旧项目包包含无法迁移的记录，未导入以免丢失数据：{member}")
        snapshots.append(member)
    missing = expected - members
    if missing:
        raise ValueError(f"旧项目包缺少版本记录：{', '.join(sorted(missing))}")

    identity = _prepare_identity(project.envelope.data, source_preparer, context)
    variants = tuple(
        _decode_variant(archive, f"{name}/current.json", VariantRef(VariantId(value), project_id), identity)
        for name, value in mapping.items()
    )
    documents = []
    used_names: set[tuple[str, str]] = set()
    for member in snapshots:
        ref = VariantRef(VariantId(mapping[PurePosixPath(member).parts[0]]), project_id)
        raw = _read_document(archive, member)
        snapshot = _decode_variant(archive, member, ref, identity, raw=raw)
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
    if not identity.verified and (
        any(variant.entries for variant in variants) or any(item["variant"]["data"]["entries"] for item in documents)
    ):
        project.envelope.data["legacy"]["archive_recovery"] = "source-baseline-required"
    validate_v2(project.envelope.to_dict(), project_ref)
    return project, variants, tuple(documents)


def _read_document(archive: ZipFile, member: str) -> dict[str, Any]:
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
            try:
                with Path(location).open("rb") as stream:
                    magic = stream.read(4)
            except FileNotFoundError:
                magic = b""
            source["format_id"] = (FormatId.SST_SSU9 if magic == b"SSU9" else FormatId.SST_SSU8).value


def _prepare_identity(data: dict[str, Any], preparer, context: RequestContext) -> _SourceIdentity:
    sources = authoritative_baseline_sources(tuple(data["sources"]), tuple(data.get("source_relations", ())))
    fingerprints = {}
    entries = {}
    keys: dict[str, set[EntryKey]] = {}
    verified = bool(sources)
    for source in sources:
        location = source["location"]
        try:
            prepared = preparer.prepare_source(
                ProjectSourceRequest(
                    location,
                    format_hint=FormatId(source["format_id"]),
                    expected_fingerprint=source.get("fingerprint"),
                    options=tuple(source["format_options"].items()),
                ),
                context,
                role="primary",
                common_options=(),
            )
        except FileNotFoundError:
            verified = False
            continue
        except DomainError as exc:
            if exc.code == "PROJECT_SOURCE_NOT_FOUND":
                verified = False
                continue
            raise ValueError(f"旧项目包来源无法验证，请恢复原始源文件后重试：{location}；{exc}") from exc
        fingerprint = prepared.baseline.fingerprint
        hydration = prepared.hydration
        if hydration is None or hydration.fingerprint != fingerprint.sha256:
            raise ValueError(f"旧项目包来源缺少可靠的旧 ID 映射，请恢复源文件后重试：{location}")
        previous = fingerprints.get(fingerprint.namespace)
        if previous is not None and previous != fingerprint:
            raise ValueError(f"旧项目包来源身份冲突：{location}")
        fingerprints[fingerprint.namespace] = fingerprint
        baseline_entries = {entry.entry_key: entry for entry in prepared.baseline.entries}
        for item in hydration.entries:
            if not item.legacy_id or item.entry_key not in baseline_entries:
                raise ValueError(f"旧项目包来源条目身份无法验证：{location}")
            keys.setdefault(item.legacy_id, set()).add(item.entry_key)
        for key, entry in baseline_entries.items():
            if key in entries and entries[key] != entry:
                raise ValueError(f"旧项目包来源包含冲突的条目：{location}")
            entries[key] = entry
        source["fingerprint"] = fingerprint.sha256
        # Keep the original content identity for retained primary/import pairs.
        source.setdefault("legacy", {}).setdefault("namespace", fingerprint.namespace.value)
    return _SourceIdentity(
        tuple(fingerprints.values()), entries, {key: frozenset(value) for key, value in keys.items()}, verified
    )


def _decode_variant(
    archive: ZipFile,
    member: str,
    ref: VariantRef,
    identity: _SourceIdentity,
    *,
    raw: dict[str, Any] | None = None,
) -> VariantSnapshot:
    source = raw if raw is not None else _read_document(archive, member)
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
    if identity.verified:
        for key, row in rows.items():
            candidates = identity.legacy_keys.get(key, frozenset())
            if len(candidates) != 1:
                raise ValueError(f"旧条目 ID 无法唯一映射到当前来源：{member}，{key!r}；请恢复匹配的原始源文件后重试")
            entry_key = next(iter(candidates))
            row["entry_key"] = entry_key.to_dict()
            row["external_refs"] = [item.to_dict() for item in identity.entries[entry_key].external_refs]
        data["source_fingerprints"] = [item.to_dict() for item in identity.fingerprints]
    elif not rows:
        data["source_fingerprints"] = []
    data["entries"] = list(rows.values())
    validated = validate_v2(document, ref)
    if not isinstance(validated, VariantDto):
        raise ValueError(f"旧项目包版本记录无效：{member}")
    snapshot = VariantSnapshot.from_dto(validated, ref)
    validate_v2(snapshot.to_dto().envelope.to_dict(), ref)
    return snapshot


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


__all__ = ["decode_legacy_archive"]
