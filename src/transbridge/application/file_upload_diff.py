# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Pure, file-scoped upload differences across ParaTranz key generations."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .io.identity import SourceNamespace
from .io.paratranz_context_order import parse_ordered_context
from .io.paratranz_keys import decode_key, split_ordered_key

_MODES = {"orig_only", "trans_safe", "trans_force", "both"}
_LITERAL_PREFIX = "__transbridge_key__:v1:k:"
_QUALIFIED_PREFIX = "__transbridge_key__:v1:o:"


class IncompleteUploadSnapshot(ValueError):
    """An archive member needs a fresh file read before planning any writes."""


@dataclass(frozen=True, slots=True)
class FileUploadChanges:
    renames: tuple[dict, ...] = ()
    source_changed: bool = False
    translations: tuple[dict, ...] = ()


def plan_file_changes(
    records: Sequence[Mapping],
    cloud_rows: Sequence[Mapping],
    *,
    file_id: int,
    mode: str,
    from_archive: bool = False,
) -> FileUploadChanges:
    """Compare transport identities; never mutate local records or remote rows.

    Renames precede the incremental full-file source upload. Translation-only
    imports retain the complete cloud key. Combined imports use the final key
    and also restore translations on rows affected by a source change.
    """
    if mode not in _MODES:
        raise ValueError("未知文件上传模式。")
    if type(file_id) is not int or file_id < 1:
        raise ValueError("file_id must be a positive integer")
    local = _validate_rows(records, file_id=None, from_archive=False)
    cloud = _validate_rows(cloud_rows, file_id=file_id, from_archive=from_archive)
    matches = _match_rows(local, cloud)
    source_mode = mode in {"orig_only", "both"}
    renames, translations = [], []
    source_changed = False
    for index, record in enumerate(local):
        remote = matches.get(index)
        if remote is None:
            source_changed |= source_mode
            if mode == "both":
                translations.append(_translation(record, record["key"]))
            continue
        transport, _ = split_ordered_key(record["key"])
        if transport.startswith(_QUALIFIED_PREFIX):
            _require(remote, ("original",), from_archive)
            decode_key(transport, remote["original"], SourceNamespace.legacy())
        source_dirty = False
        if source_mode:
            _require(remote, ("original", "context"), from_archive, optional_context=True)
            context, old_order = parse_ordered_context(remote.get("context", ""))
            local_context, _ = parse_ordered_context(record.get("context", ""))
            source_dirty = remote["original"] != record["original"] or (context or "") != (local_context or "")
            source_changed |= source_dirty
            if remote["key"] != record["key"] or old_order is not None:
                _require(remote, ("id", "stage"), from_archive)
                if type(remote["id"]) is not int or remote["id"] < 1:
                    error = IncompleteUploadSnapshot if from_archive else ValueError
                    raise error("云端词条缺少有效 ID，无法更新 key 或清理旧显示编号。")
                if type(remote["stage"]) is not int:
                    error = IncompleteUploadSnapshot if from_archive else ValueError
                    raise error("云端词条缺少有效状态，无法在更新 key 时保留状态。")
                update = {"id": remote["id"], "key": record["key"], "stage": remote["stage"]}
                if old_order is not None:
                    update["context"] = context
                renames.append(update)
        if mode == "orig_only":
            continue
        if mode == "trans_force" and not record.get("translation", "").strip():
            continue
        fields = ("translation",) if mode == "trans_force" else ("translation", "stage")
        _require(remote, fields, from_archive)
        different = record.get("translation", "") != remote["translation"]
        if mode != "trans_force":
            different |= record.get("stage", 0) != remote["stage"]
        if different or (mode == "both" and source_dirty):
            key = record["key"] if source_mode else remote["key"]
            translations.append(_translation(record, key))
    return FileUploadChanges(tuple(renames), source_changed, tuple(translations))


def _translation(record: Mapping, key: str) -> dict:
    return {"key": key, "translation": record.get("translation", ""), "stage": record.get("stage", 0)}


def _require(row, fields, from_archive, *, optional_context=False):
    missing = [field for field in fields if field not in row and not (optional_context and field == "context")]
    if from_archive and optional_context and "context" not in row:
        missing.append("context")
    for field in fields:
        if field not in row:
            continue
        value = row[field]
        if (
            (field == "original" and not isinstance(value, str))
            or (field in {"translation", "context"} and value is not None and not isinstance(value, str))
            or (field == "stage" and value is not None and type(value) is not int)
        ):
            missing.append(field)
    if missing:
        error = IncompleteUploadSnapshot if from_archive else ValueError
        raise error(f"云端文件词条缺少有效 {', '.join(missing)}，无法安全比较或更新。")


def _validate_rows(rows, *, file_id, from_archive):
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("文件词条必须为记录序列。")
    keys, ids, validated = set(), set(), []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("文件词条数据无效。")
        _require(row, ("key",), from_archive)
        key = row["key"]
        if not isinstance(key, str) or not key.strip():
            raise ValueError("文件词条 key 无效。")
        if key in keys:
            raise ValueError("同一文件存在重复 key，无法安全匹配。")
        keys.add(key)
        remote_id = row.get("id")
        if remote_id is not None:
            if type(remote_id) is not int or remote_id < 1:
                raise ValueError("云端词条 ID 必须为正整数。")
            if remote_id in ids:
                raise ValueError("云端文件存在重复词条 ID，无法安全匹配。")
            ids.add(remote_id)
        remote_file = row.get("fileId", row.get("file"))
        if isinstance(remote_file, Mapping):
            remote_file = remote_file.get("id")
        if remote_file is not None and (type(remote_file) is not int or remote_file < 1 or remote_file != file_id):
            raise ValueError("云端返回了目标文件以外的词条。")
        validated.append(row)
    return validated


def _match_rows(local, cloud):
    """Permit old literal keys only when they prove one unique match."""
    candidates = {}
    local_identities = set()
    for index, row in enumerate(local):
        transport, _ = split_ordered_key(row["key"])
        if transport in local_identities:
            raise ValueError("本地文件存在重复传输身份。")
        local_identities.add(transport)
        if transport.startswith(_QUALIFIED_PREFIX):
            _require(row, ("original",), False)
            decode_key(transport, row["original"], SourceNamespace.legacy())
        aliases = [transport]
        if transport.startswith(_LITERAL_PREFIX):
            aliases.append(decode_key(transport, "", SourceNamespace.legacy()).local_key)
        for alias in aliases:
            candidates.setdefault(alias, set()).add(index)
    matched = {}
    cloud_identities = set()
    for row in cloud:
        transport, _ = split_ordered_key(row["key"])
        if transport in cloud_identities:
            raise ValueError("云端文件存在重复传输身份，无法安全匹配。")
        cloud_identities.add(transport)
        indexes = candidates.get(transport, set()) | candidates.get(row["key"], set())
        if len(indexes) > 1:
            raise ValueError("云端 key 与多个本地传输身份匹配，无法消除歧义。")
        if indexes:
            index = next(iter(indexes))
            if index in matched:
                raise ValueError("多个云端词条匹配同一本地传输身份，无法消除歧义。")
            matched[index] = row
    return matched
