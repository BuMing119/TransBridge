"""Reuse immutable source parses only while all input content remains identical."""

from collections import OrderedDict
from dataclasses import dataclass, fields, is_dataclass
import hashlib
import json
from pathlib import Path
import sys
from threading import RLock

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryKey
from transbridge.application.security.paths import PathAuthorizationPolicy, PathGrant
from transbridge.parser.strings_file import PluginStringsLookup
from transbridge.persistence.v2.variant import SourceFingerprint


@dataclass(frozen=True, slots=True)
class SourceReadEntry:
    entry_key: EntryKey
    legacy_id: str
    original: str
    context: str | None
    metadata: tuple
    string_id: int | None


@dataclass(frozen=True, slots=True)
class SourceReadHydration:
    """Read-only text projection; never retains plugin bytes or write templates."""

    fingerprint: SourceFingerprint
    entries: tuple[SourceReadEntry, ...]


@dataclass(frozen=True, slots=True)
class _CachedHydration:
    signature: tuple
    read_model: SourceReadHydration
    entry_count: int
    estimated_bytes: int


class SourceHydrationCache:
    """Bounded per-service parse cache; translations are never cached here.

    Every lookup hashes source bytes and loose string companions, including
    missing companions. BSA dependencies hash the actual matching string
    members, not the entire archive or a weak mtime/size approximation.
    """

    def __init__(self, *, max_sources=128, max_entries=250_000, max_bytes=128 * 1024 * 1024):
        self._items = OrderedDict()
        self._lock = RLock()
        self._max_sources = max_sources
        self._max_entries = max_entries
        self._entry_count = 0
        self._estimated_bytes = 0
        self._max_bytes = max_bytes

    def prepare(self, preparer, request, context, *, scope_key):
        authority = (
            context.owner_id,
            context.session_id,
            context.project_id,
            context.variant_id,
            tuple(sorted(context.permissions)),
            context.authorized_roots,
            context.metadata,
        )
        key = (scope_key, authority, json.dumps(request.fingerprint_data(), ensure_ascii=False, sort_keys=True))
        with self._lock:
            try:
                signature = source_input_signature(request, context)
            except OSError:
                # The normal parser owns actionable missing/permission errors.
                return _read_model(preparer.prepare_source(request, context, role="primary", common_options=()))
            if request.expected_fingerprint is not None and signature[0] != request.expected_fingerprint:
                raise DomainError(
                    ErrorCategory.CONFLICT,
                    "PROJECT_SOURCE_FINGERPRINT_MISMATCH",
                    "工程来源内容与预期指纹不一致。",
                )
            cached = self._items.get(key)
            if cached is not None and cached.signature == signature:
                self._items.move_to_end(key)
                return cached.read_model
            if cached is not None:
                self._discard(key)
            prepared = preparer.prepare_source(request, context, role="primary", common_options=())
            read_model = _read_model(prepared)
            del prepared
            if signature != source_input_signature(request, context):
                raise DomainError(
                    ErrorCategory.CONFLICT,
                    "CATEGORIZED_SOURCE_CHANGED",
                    "工程来源或字符串依赖在读取期间变化，未采用不完整内容；可在当前窗口重试。",
                )
            if read_model is not None:
                entry_count = len(read_model.entries)
                estimated_bytes = _immutable_size(read_model, set())
                if (
                    entry_count <= self._max_entries
                    and estimated_bytes is not None
                    and estimated_bytes <= self._max_bytes
                ):
                    self._items[key] = _CachedHydration(signature, read_model, entry_count, estimated_bytes)
                    self._entry_count += entry_count
                    self._estimated_bytes += estimated_bytes
                    while (
                        len(self._items) > self._max_sources
                        or self._entry_count > self._max_entries
                        or self._estimated_bytes > self._max_bytes
                    ):
                        self._discard(next(iter(self._items)))
            return read_model

    def _discard(self, key):
        removed = self._items.pop(key)
        self._entry_count -= removed.entry_count
        self._estimated_bytes -= removed.estimated_bytes


def source_input_signature(request, context=None):
    policy = _path_policy(context)
    _authorize(request.location, policy)
    path = Path(request.location).resolve(strict=True)
    source_hash = _file_hash(path)
    options = dict(request.options)
    dependencies = []
    plugin = request.format_hint is FormatId.PLUGIN_SSE or (
        request.format_hint is None and path.suffix.lower() in {".esp", ".esm", ".esl"}
    )
    if plugin and options.get("discover_sibling_strings", True):
        language = str(options.get("language", "english"))
        prefix = f"{path.stem}_{language.capitalize()}"
        for extension in ("strings", "dlstrings", "ilstrings"):
            dependency = path.parent / "Strings" / f"{prefix}.{extension}"
            if dependency.exists():
                _authorize(dependency, policy)
            dependencies.append((str(dependency), _file_hash(dependency) if dependency.exists() else None))
        # Mirror the parser's archive lookup; changing unrelated archive assets
        # cannot affect a source's translatable strings.
        base_game = PluginStringsLookup._BASE_GAME_PLUGINS
        archive = path.parent / ("Skyrim - Interface.bsa" if path.stem.lower() in base_game else f"{path.stem}.bsa")
        if archive.exists():
            _authorize(archive, policy)
        dependencies.append((
            str(archive),
            _archive_strings_hash(archive, path.stem, language) if archive.exists() else None,
        ))
    return source_hash, tuple(dependencies)


def _file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _path_policy(context):
    if context is None or not context.authorized_roots:
        return None  # The existing trusted desktop context has no root restriction.
    try:
        return PathAuthorizationPolicy(tuple(PathGrant(Path(root)) for root in context.authorized_roots))
    except (OSError, RuntimeError) as exc:
        raise DomainError(
            ErrorCategory.PERMISSION,
            "SOURCE_ROOT_GRANT_INVALID",
            "来源读取授权目录已不可用，本次未读取文件。",
            cause=exc,
        ) from exc


def _authorize(path, policy):
    if policy is None:
        return
    decision = policy.authorize(path, working_directory=Path.cwd())
    if not decision.allowed:
        raise DomainError(
            ErrorCategory.PREREQUISITE if decision.code == "PATH_NOT_FOUND" else ErrorCategory.PERMISSION,
            decision.code,
            f"无法读取工程来源或字符串依赖：{decision.reason}。",
        )


def _archive_strings_hash(path, stem, language):
    from sse_bsa import bsa_archive

    try:
        archive = bsa_archive.BSAArchive(path)
        matches = [
            member
            for member in archive.glob(f"strings/{stem.lower()}_{language.lower()}.*")
            if member.lower().endswith((".strings", ".dlstrings", ".ilstrings"))
        ]
        result = []
        for member in matches:
            with archive.get_file_stream(member) as stream:
                result.append((member, hashlib.file_digest(stream, "sha256").hexdigest()))
        return tuple(result)
    except Exception:
        # Malformed archives are ignored by the existing parser. Fingerprint
        # their full bytes so replacing one can never reuse a stale parse.
        return ("unreadable_archive", _file_hash(path))


def _read_model(prepared):
    if prepared.hydration is None:
        return None
    return SourceReadHydration(
        prepared.baseline.fingerprint,
        tuple(
            SourceReadEntry(
                entry.entry_key, entry.legacy_id, entry.original, entry.context, entry.metadata, entry.string_id
            )
            for entry in prepared.hydration.entries
        ),
    )


def _immutable_size(value, seen):
    """Conservative retained-object estimate; mutable metadata disables caching."""
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        return size
    if isinstance(value, (tuple, frozenset)):
        members = value
    elif is_dataclass(value) and value.__dataclass_params__.frozen:
        members = (getattr(value, field.name) for field in fields(value))
    else:
        return None
    for member in members:
        estimated = _immutable_size(member, seen)
        if estimated is None:
            return None
        size += estimated
    return size
