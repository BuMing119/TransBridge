"""Immutable generated-term records and bounded views of one publication."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Any

from transbridge.application.terminology.identity import normalize_original

from .term_formats import TermEntry


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported shared terminology metadata: {type(value).__name__}")


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class FrozenTermEntry:
    """Read-only TermEntry shape; mutable compatibility copies are explicit."""

    term: str
    translation: str
    source: str
    context: str
    created_at: str
    case_sensitive: bool
    variants: tuple[str, ...]
    pos: str
    note: str
    external_id: int | str | None
    metadata: Mapping[str, Any]

    def __post_init__(self):
        object.__setattr__(self, "variants", tuple(self.variants))
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    @classmethod
    def capture(cls, entry: TermEntry | FrozenTermEntry) -> FrozenTermEntry:
        if isinstance(entry, cls):
            return entry
        values = {item.name: getattr(entry, item.name) for item in fields(TermEntry)}
        return cls(**values)

    def mutable_copy(self) -> TermEntry:
        values = {item.name: getattr(self, item.name) for item in fields(TermEntry)}
        values["variants"] = list(self.variants)
        values["metadata"] = _thaw(self.metadata)
        return TermEntry(**values)


@dataclass(frozen=True, slots=True, init=False)
class SharedTermSnapshot:
    """One frozen scope with a stable identity across cache eviction/recreation.

    ``entries`` retains the historical detached, mutable TermEntry API. Internal
    lookup paths use ``records``/``records_by_term`` without making full copies.
    """

    revision: int
    records: tuple[FrozenTermEntry, ...]
    records_by_term: Mapping[str, FrozenTermEntry] = field(compare=False, repr=False)
    cache_identity: object = field(compare=False, repr=False)

    def __init__(self, revision: int, entries: Iterable[TermEntry | FrozenTermEntry], *, cache_identity=None):
        records = tuple(FrozenTermEntry.capture(entry) for entry in entries)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "records_by_term", MappingProxyType({entry.term: entry for entry in records}))
        object.__setattr__(self, "cache_identity", object() if cache_identity is None else cache_identity)

    @property
    def entries(self) -> tuple[TermEntry, ...]:
        return tuple(entry.mutable_copy() for entry in self.records)


class SharedTermSnapshots:
    """Normalize and freeze once per transaction; reuse at most 16 scope views.

    The unique token belongs to this transaction, never to the revision number:
    a failed acknowledgement can roll back and later reuse the same revision.
    The owning pool holds its lock for every access to this index.
    """

    def __init__(self, revision: int, entries: Iterable[TermEntry], capacity: int = 16):
        self.revision = revision
        self._token = object()
        self._capacity = capacity
        self._scopes = {}
        self._views = OrderedDict()
        for entry in entries:
            scope = entry.metadata.get("shared_plugin_id")
            self._scopes.setdefault(scope, {})[normalize_original(entry.term)] = FrozenTermEntry.capture(entry)

    def snapshot(self, plugin_id: str | None) -> SharedTermSnapshot:
        if plugin_id in self._views:
            self._views.move_to_end(plugin_id)
            return self._views[plugin_id]
        terms = self._scopes.get(None, {})
        if plugin_id is not None and plugin_id in self._scopes:
            terms = terms | self._scopes[plugin_id]
        result = SharedTermSnapshot(self.revision, terms.values(), cache_identity=(self._token, plugin_id))
        self._views[plugin_id] = result
        while len(self._views) > self._capacity:
            self._views.popitem(last=False)
        return result
