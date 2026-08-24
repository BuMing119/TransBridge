"""Search-only projection over the shell action catalog.

The palette never decides whether an application action is enabled and never
constructs its business command.  It consumes :class:`ActionAvailability`
values owned by the composition layer and returns a stable intent request.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
import unicodedata

from transbridge.ui.shell.action_catalog import (
    DEFAULT_ACTION_CATALOG,
    ActionAvailability,
    ActionCatalog,
    DangerLevel,
    IntentId,
    IntentPlacement,
)


class CommandCandidateKind(StrEnum):
    ACTION = "action"
    RECENT_PROJECT = "recent-project"
    TRANSLATION_CONTENT = "translation-content"


@dataclass(frozen=True, slots=True)
class DynamicCommandCandidate:
    """A recent/contextual target supplied by an authoritative projection.

    ``stale_reason`` is target validity, not action availability.  The latter
    remains owned by the model's ``AvailabilitySource``.
    """

    candidate_id: str
    kind: CommandCandidateKind
    label: str
    intent_id: IntentId
    aliases: tuple[str, ...] = ()
    payload: Mapping[str, str] = MappingProxyType({})
    stale_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("dynamic candidate ID must not be empty")
        if self.kind is CommandCandidateKind.ACTION:
            raise ValueError("dynamic candidate kind must describe a contextual target")
        if not self.label.strip():
            raise ValueError("dynamic candidate label must not be empty")
        if any(not alias.strip() for alias in self.aliases):
            raise ValueError("dynamic aliases must not contain empty values")
        if self.stale_reason is not None and not self.stale_reason.strip():
            raise ValueError("stale candidate requires a user-facing reason")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class CommandSearchResult:
    result_id: str
    label: str
    kind: CommandCandidateKind
    availability: ActionAvailability
    aliases: tuple[str, ...]
    payload: Mapping[str, str]
    match_rank: int
    stable_rank: int

    @property
    def intent_id(self) -> IntentId:
        return self.availability.descriptor.intent_id

    @property
    def disabled_reason(self) -> str | None:
        return self.availability.reason


@dataclass(frozen=True, slots=True)
class CommandSearchSnapshot:
    revision: int
    query: str
    results: tuple[CommandSearchResult, ...]


@dataclass(frozen=True, slots=True)
class CommandIntentRequest:
    """A presentation request; the shell still owns dispatch and confirmation."""

    intent_id: IntentId
    payload: Mapping[str, str]
    requires_confirmation: bool
    source_result_id: str


@dataclass(frozen=True, slots=True)
class CommandActivation:
    request: CommandIntentRequest | None
    blocked_reason: str | None = None


AvailabilitySource = Callable[[], Iterable[ActionAvailability]]
DynamicCandidateSource = Callable[[], Iterable[DynamicCommandCandidate]]


def _normalise(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _match_rank(query: str, values: tuple[str, ...]) -> int | None:
    if not query:
        return 0
    tokens = query.split()
    best: int | None = None
    for value_index, value in enumerate(values):
        normalised = _normalise(value)
        if not normalised:
            continue
        if normalised == query:
            rank = 0 + value_index
        elif normalised.startswith(query):
            rank = 10 + value_index
        elif all(token in normalised for token in tokens):
            rank = 20 + value_index
        else:
            continue
        best = rank if best is None else min(best, rank)
    return best


class CommandPaletteModel:
    """Qt-free command search model with deterministic result ordering."""

    def __init__(
        self,
        availability_source: AvailabilitySource,
        *,
        dynamic_source: DynamicCandidateSource | None = None,
        catalog: ActionCatalog = DEFAULT_ACTION_CATALOG,
    ) -> None:
        self._catalog = catalog
        self._availability_source = availability_source
        self._dynamic_source = dynamic_source or (lambda: ())
        self._revision = 0

    def search(self, query: str = "") -> CommandSearchSnapshot:
        normalised_query = _normalise(query)
        availability = self._read_availability()
        results: list[CommandSearchResult] = []

        for stable_rank, descriptor in enumerate(self._catalog.all()):
            current = availability.get(descriptor.intent_id)
            if current is None:
                continue
            rank = _match_rank(normalised_query, (descriptor.label, *descriptor.aliases, descriptor.intent_id.value))
            if rank is None:
                continue
            placement_rank = {
                IntentPlacement.PRIMARY: 0,
                IntentPlacement.CONTEXTUAL: 1,
                IntentPlacement.SECONDARY: 2,
            }[descriptor.placement]
            results.append(
                CommandSearchResult(
                    result_id=f"action:{descriptor.intent_id.value}",
                    label=descriptor.label,
                    kind=CommandCandidateKind.ACTION,
                    availability=current,
                    aliases=descriptor.aliases,
                    payload=MappingProxyType({}),
                    match_rank=rank,
                    stable_rank=1000 + placement_rank * 100 + stable_rank,
                )
            )

        seen_dynamic_ids: set[str] = set()
        for stable_rank, candidate in enumerate(self._dynamic_source()):
            if candidate.candidate_id in seen_dynamic_ids:
                raise ValueError(f"duplicate dynamic candidate ID: {candidate.candidate_id}")
            seen_dynamic_ids.add(candidate.candidate_id)
            descriptor = self._catalog.get(candidate.intent_id)
            current = availability.get(candidate.intent_id)
            if current is None:
                continue
            rank = _match_rank(normalised_query, (candidate.label, *candidate.aliases, descriptor.label))
            if rank is None:
                continue
            if candidate.stale_reason is not None:
                current = ActionAvailability(descriptor, enabled=False, reason=candidate.stale_reason)
            results.append(
                CommandSearchResult(
                    result_id=f"dynamic:{candidate.candidate_id}",
                    label=candidate.label,
                    kind=candidate.kind,
                    availability=current,
                    aliases=candidate.aliases,
                    payload=candidate.payload,
                    match_rank=rank,
                    stable_rank=stable_rank,
                )
            )

        results.sort(key=lambda item: (item.match_rank, item.stable_rank, item.result_id))
        self._revision += 1
        return CommandSearchSnapshot(self._revision, query, tuple(results))

    def _read_availability(self) -> dict[IntentId, ActionAvailability]:
        values: dict[IntentId, ActionAvailability] = {}
        for current in self._availability_source():
            intent_id = current.descriptor.intent_id
            if intent_id in values:
                raise ValueError(f"duplicate action availability: {intent_id.value}")
            if self._catalog.get(intent_id) != current.descriptor:
                raise ValueError("action availability must use the configured action catalog")
            values[intent_id] = current
        return values


class CommandPaletteController:
    """One-shot palette session that forwards at most one stable intent."""

    def __init__(self, model: CommandPaletteModel) -> None:
        self._model = model
        self._snapshot: CommandSearchSnapshot | None = None
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self, query: str = "") -> CommandSearchSnapshot:
        self._open = True
        self._snapshot = self._model.search(query)
        return self._snapshot

    def set_query(self, query: str) -> CommandSearchSnapshot:
        if not self._open:
            raise RuntimeError("command palette is closed")
        self._snapshot = self._model.search(query)
        return self._snapshot

    def activate(self, result_id: str) -> CommandActivation:
        if not self._open or self._snapshot is None:
            return CommandActivation(None, "命令搜索已关闭")
        # Re-read both authoritative availability and dynamic projections at
        # the point of activation so a removed recent target cannot be used
        # from a stale list item.
        self._snapshot = self._model.search(self._snapshot.query)
        result = next((item for item in self._snapshot.results if item.result_id == result_id), None)
        if result is None:
            return CommandActivation(None, "搜索结果已失效，请重新搜索")
        if not result.availability.enabled:
            return CommandActivation(None, result.availability.reason)

        self._open = False
        descriptor = result.availability.descriptor
        return CommandActivation(
            CommandIntentRequest(
                intent_id=descriptor.intent_id,
                payload=result.payload,
                requires_confirmation=descriptor.danger is not DangerLevel.SAFE,
                source_result_id=result.result_id,
            )
        )

    def close(self) -> None:
        self._open = False
        self._snapshot = None


__all__ = [
    "AvailabilitySource",
    "CommandActivation",
    "CommandCandidateKind",
    "CommandIntentRequest",
    "CommandPaletteController",
    "CommandPaletteModel",
    "CommandSearchResult",
    "CommandSearchSnapshot",
    "DynamicCandidateSource",
    "DynamicCommandCandidate",
]
