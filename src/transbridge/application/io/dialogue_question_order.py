# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Order explicit questions and their responses inside one trusted dialogue topic."""

from collections.abc import Iterable
import re
from typing import Protocol

from .identity import EntryKey
from .paratranz_context_order import parse_ordered_context

_FORM_ID = re.compile(r"[0-9A-Fa-f]{8}\Z")
_CONTEXT = re.compile(r"(DIAL|INFO):([A-Z0-9_]{4})(?:\|([0-9A-Fa-f]{8}|))?\Z")
_KEY = re.compile(r"[^:]*:([0-9A-Fa-f]{8})\|[0-9]+(?:~([A-Z0-9_]{4}):([A-Z0-9_]{4})(?:\|([0-9A-Fa-f]{8}|))?)?\Z")


class _QuestionEntry(Protocol):
    @property
    def identity(self) -> EntryKey: ...

    @property
    def context(self) -> str | None: ...


def order_topic_questions[EntryT: _QuestionEntry](entries: Iterable[EntryT]) -> tuple[EntryT, ...]:
    """Keep every original row while ordering questions within one known topic.

    A visible DIAL FULL supplies the default question. Its INFO responses precede
    INFOs with their own visible RNAM override. Cohorts and response strings keep
    source order. Without a visible FULL, INFO groups retain their source order.
    Unidentified rows retain their exact slots; no text is compared or inferred.
    """
    ordered = list(entries)
    slots, fulls = [], []
    groups = {}
    for index, entry in enumerate(ordered):
        context, _ = parse_ordered_context(entry.context)
        match = _CONTEXT.fullmatch(context) if isinstance(context, str) else None
        if match is None:
            continue
        kind, field, quest = match[1], match[2], (match[3] or "").upper()
        if kind == "DIAL" and field == "FULL":
            slots.append(index)
            fulls.append(entry)
        elif kind == "INFO" and (form_id := _info_form_id(entry, field, quest)):
            slots.append(index)
            identity = (entry.identity.namespace.value, quest, form_id)
            groups.setdefault(identity, []).append((entry, field))
    cohorts = [[], []]
    has_default = any(_has_original(entry) for entry in fulls)
    for members in groups.values():
        overridden = has_default and any(field == "RNAM" and _has_original(entry) for entry, field in members)
        cohorts[bool(overridden)].extend(_prompt_first(members))
    for index, entry in zip(slots, fulls + cohorts[0] + cohorts[1], strict=True):
        ordered[index] = entry
    return tuple(ordered)


def _has_original(entry: _QuestionEntry) -> bool:
    original = getattr(entry, "original", None)
    return bool(original.strip()) if isinstance(original, str) else getattr(entry, "has_original", False)


def _info_form_id(entry: _QuestionEntry, field: str, quest: str) -> str:
    key = _KEY.fullmatch(entry.identity.local_key)
    if key is not None and (
        (key[2] is not None and (key[2] != "INFO" or key[3] != field))
        or (key[4] is not None and key[4].upper() != quest)
    ):
        return ""
    explicit = (getattr(entry, "form_id_with_plugin", None) or "").partition("|")[0]
    form_id = explicit.upper() if _FORM_ID.fullmatch(explicit) else (key[1].upper() if key else "")
    return "" if key is not None and key[1].upper() != form_id else form_id


def _prompt_first(members):
    prompts = [entry for entry, field in members if field == "RNAM"]
    responses = [entry for entry, field in members if field == "NAM1"]
    selected = iter(prompts + responses)
    return [next(selected) if field in {"RNAM", "NAM1"} else entry for entry, field in members]
