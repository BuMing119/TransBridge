"""Immutable terminal-request details with a small, ordered Session index."""

from copy import deepcopy
import json

from transbridge.persistence.v2.schema import parse_json_bytes, serialize_document

from .models import RequestError, UserRequest, digest
from .transcript import AttachmentRef, TranscriptManifest

_ARCHIVE_KIND = "assistant_request_archive"


def _error(message):
    return RequestError("REQUEST_ARCHIVE_INVALID", message)


def _archives(state):
    records = state.get("request_archives", [])
    if not isinstance(records, list):
        raise _error("请求归档索引必须是列表。")
    identities, positions = set(), set()
    for entry in records:
        if not isinstance(entry, dict) or type(entry.get("version")) is not int or entry["version"] != 1:
            raise _error("请求归档索引版本不受支持。")
        identity, position = entry.get("request_id"), entry.get("position")
        if (
            not isinstance(identity, str)
            or not identity
            or identity in identities
            or type(position) is not int
            or position < 0
            or position in positions
        ):
            raise _error("请求归档索引身份或顺序不一致。")
        identities.add(identity)
        positions.add(position)
    return records


def _request(raw, session_id):
    try:
        request = UserRequest.from_dict(raw)
    except (ValueError, TypeError, KeyError) as exc:
        raise _error("请求归档包含无效的请求详情。") from exc
    if request.session_id != session_id:
        raise _error("请求归档不属于当前会话。")
    return request


def hydrate_request_state(state, session_id, store):
    """Restore full details after the caller has authorized the owning Session.

    The returned state keeps its archive indexes so a subsequent CAS can replace
    only changed artifacts. Hydrating an already hydrated state is idempotent.
    """
    restored = deepcopy(state)
    requests = restored.get("requests", [])
    if not isinstance(requests, list):
        raise _error("会话请求必须是列表。")
    known = set()
    for value in requests:
        identity = _request(value, session_id).request_id
        if identity in known:
            raise _error("会话请求身份重复。")
        known.add(identity)
    entries = sorted(_archives(restored), key=lambda entry: entry["position"])
    full_count = len(requests) + sum(entry["request_id"] not in known for entry in entries)
    if any(entry["position"] >= full_count for entry in entries):
        raise _error("请求归档位置超出了会话请求范围。")
    if entries:
        restored["requests"] = requests
    if entries and store is None:
        raise _error("读取归档请求需要附件存储。")
    for entry in entries:
        if entry.get("session_id") != session_id:
            raise _error("请求归档索引不属于当前会话。")
        try:
            reference = AttachmentRef.from_dict(entry["artifact"])
            document = parse_json_bytes(store.read_artifact(session_id, reference))
            if (
                document.get("kind") != _ARCHIVE_KIND
                or type(document.get("version")) is not int
                or document["version"] != 1
            ):
                raise _error("请求归档详情格式不受支持。")
            request = _request(document["request"], session_id)
        except (ValueError, TypeError, KeyError) as exc:
            raise _error("请求归档详情无法验证。") from exc
        if (
            not request.terminal
            or request.unsettled
            or document.get("session_id") != session_id
            or document.get("request_id") != request.request_id
            or request.request_id != entry["request_id"]
            or request.revision != entry.get("revision")
            or request.status != entry.get("status")
            or digest(request.to_dict()) != entry.get("request_digest")
        ):
            raise _error("请求归档详情与已提交索引不一致。")
        if request.request_id not in known:
            if entry["position"] > len(requests):
                raise _error("请求归档位置超出了会话请求范围。")
            requests.insert(entry["position"], request.to_dict())
            known.add(request.request_id)
    return restored


def _terminal_sequences(state, previous):
    sequences = {identity: entry.get("terminal_sequence", 0) for identity, entry in previous.items()}
    for event in state.get("lifecycle_events", ()):
        if event.get("after", {}).get("status") in {"completed", "failed", "cancelled", "superseded"} and event.get(
            "before", {}
        ).get("status") not in {"completed", "failed", "cancelled", "superseded"}:
            for request_id in event.get("request_ids", ()):
                sequences[request_id] = max(sequences.get(request_id, 0), event["sequence"])
    return sequences


def compact_request_state(state, manifest, session_id, store, *, keep_recent_terminal=100):
    """Prepare immutable details before the caller atomically publishes its CAS.

    Nothing here marks a Session committed. Failed CAS artifacts remain harmless
    orphans, and previous manifests and backups continue to read their old data.
    """
    if type(keep_recent_terminal) is not int or keep_recent_terminal < 0:
        raise ValueError("keep_recent_terminal must be a nonnegative integer")
    restored = hydrate_request_state(state, session_id, store)
    if "requests" not in restored:
        return restored, manifest
    previous = {entry["request_id"]: entry for entry in _archives(restored)}
    sequences = _terminal_sequences(restored, previous)
    requests = [_request(value, session_id) for value in restored["requests"]]
    terminal = [
        (sequences.get(request.request_id, 0), position)
        for position, request in enumerate(requests)
        if request.terminal and not request.unsettled
    ]
    terminal.sort()
    positions = {position for _, position in terminal[: max(0, len(terminal) - keep_recent_terminal)]}
    if positions and store is None:
        raise _error("归档请求需要附件存储。")
    indexes, references = [], []
    for position, request in enumerate(requests):
        if position not in positions:
            continue
        old = previous.get(request.request_id, {})
        request_digest = digest(request.to_dict())
        if old.get("request_digest") == request_digest:
            reference = AttachmentRef.from_dict(old["artifact"])
        else:
            reference = store.write_artifact(
                session_id,
                serialize_document({
                    "kind": _ARCHIVE_KIND,
                    "version": 1,
                    "session_id": session_id,
                    "request_id": request.request_id,
                    "request": request.to_dict(),
                }),
            )
        indexes.append({
            "version": 1,
            "request_id": request.request_id,
            "session_id": session_id,
            "revision": request.revision,
            "status": request.status.value,
            "goal": request.goal[:160],
            "item_count": len(request.items),
            "position": position,
            "terminal_sequence": sequences.get(request.request_id, 0),
            "request_digest": request_digest,
            "artifact": reference.to_dict(),
        })
        references.append(reference)
    restored["requests"] = [request.to_dict() for position, request in enumerate(requests) if position not in positions]
    if indexes or "request_archives" in restored:
        restored["request_archives"] = indexes
    if "request_summaries" in restored:
        for entry in indexes:
            restored["request_summaries"].pop(entry["request_id"], None)
    old_paths = {entry["artifact"]["path"] for entry in previous.values()}
    # Other state may explicitly retain the same artifact; do not drop its manifest ref.
    other_state = {key: value for key, value in restored.items() if key != "request_archives"}
    encoded = json.dumps(other_state, ensure_ascii=False)
    retained = [ref for ref in manifest.artifacts if ref.path not in old_paths or ref.path in encoded]
    artifacts = {reference.path: reference for reference in (*retained, *references)}
    compacted = TranscriptManifest(
        manifest.segments, manifest.last_sequence, manifest.input_watermark, tuple(artifacts.values())
    )
    return restored, compacted
