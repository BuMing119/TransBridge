"""Qt-free filtering state and projection for the Workbench preview."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from transbridge.converter.translation_entry import TranslationEntry

_CONTEXT_TO_CATEGORY: dict[str, str] = {}
_CATEGORY_CONTEXTS = {
    "人名": {"NPC_:FULL", "NPC_:SHRT", "TACT:FULL"},
    "地名": {"CELL:FULL", "DOOR:FULL", "LCTN:FULL", "REFR:FULL", "WRLD:FULL"},
    "书名": {"BOOK:FULL"},
    "书籍内容": {"BOOK:DESC"},
    "互动": {"FLOR:RNAM", "FURN:FULL", "HAZD:FULL"},
    "任务日志": {"QUST:FULL", "QUST:NNAM"},
    "法术技能": {
        "ENCH:FULL",
        "EXPL:FULL",
        "MESG:DESC",
        "MESG:FULL",
        "MESG:ITXT",
        "MGEF:DNAM",
        "MGEF:FULL",
        "PERK:FULL",
        "SHOU:FULL",
        "SPEL:DESC",
        "SPEL:FULL",
    },
    "物品": {
        "ACTI:FULL",
        "ACTI:RNAM",
        "ALCH:FULL",
        "AMMO:FULL",
        "ARMO:DESC",
        "ARMO:FULL",
        "CONT:FULL",
        "INGR:FULL",
        "KEYM:FULL",
        "MISC:FULL",
        "SLGM:FULL",
        "TREE:FULL",
        "WEAP:DESC",
        "WEAP:FULL",
    },
}
for _category, _contexts in _CATEGORY_CONTEXTS.items():
    for _context in _contexts:
        _CONTEXT_TO_CATEGORY[_context] = _category


ALL_CATEGORIES = (
    "人名",
    "地名",
    "书名",
    "书籍内容",
    "物品",
    "法术技能",
    "对话",
    "互动",
    "任务日志",
    "其他",
)


def entry_category(entry: TranslationEntry) -> str:
    """Return the stable display category for a translation entry."""
    context = entry.context or ""
    base = context.split("|", 1)[0]
    record = base.split(":", 1)[0]
    if record in ("INFO", "DIAL"):
        return "对话"
    return _CONTEXT_TO_CATEGORY.get(base, "其他")


@dataclass(frozen=True, slots=True)
class FilterState:
    """Immutable filter intent, independent from render generations."""

    categories: frozenset[str] = frozenset()
    stages: frozenset[int] = frozenset()
    labels: frozenset[str] = frozenset()
    search_key: str = ""
    search_original: str = ""
    search_translation: str = ""
    focus_labeled: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> FilterState:
        value = value or {}
        return cls(
            categories=frozenset(value.get("category", []) or []),
            stages=frozenset(value.get("stage", []) or []),
            labels=frozenset(value.get("label", []) or []),
            search_key=str(value.get("search_key", "") or ""),
            search_original=str(value.get("search_orig", "") or ""),
            search_translation=str(value.get("search_trans", "") or ""),
            focus_labeled=bool(value.get("focus_labeled", False)),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "category": list(self.categories),
            "stage": list(self.stages),
            "label": list(self.labels),
            "search_key": self.search_key,
            "search_orig": self.search_original,
            "search_trans": self.search_translation,
            "focus_labeled": self.focus_labeled,
        }


class FiltersPresenter:
    """Apply a filter intent without owning or copying the source collection."""

    def __init__(self) -> None:
        self._revision = 0
        self._state = FilterState()

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def state(self) -> FilterState:
        return self._state

    def update(self, state: FilterState) -> None:
        if state != self._state:
            self._state = state
            self._revision += 1

    def apply(
        self,
        entries: Iterable[TranslationEntry],
        entry_labels: Mapping[str, set[str]],
    ) -> list[TranslationEntry]:
        state = self._state
        key_kw = state.search_key.lower()
        original_kw = state.search_original.lower()
        translation_kw = state.search_translation.lower()
        result: list[TranslationEntry] = []
        for entry in entries:
            if state.categories and entry_category(entry) not in state.categories:
                continue
            if state.stages and entry.stage not in state.stages:
                continue
            if key_kw and key_kw not in (entry.key or "").lower():
                continue
            if original_kw and original_kw not in (entry.original or "").lower():
                continue
            if translation_kw and translation_kw not in (entry.translation or "").lower():
                continue
            labels = entry_labels.get(entry.id, set()) if entry.id else set()
            if state.labels and not labels.intersection(state.labels):
                continue
            if state.focus_labeled and not labels:
                continue
            result.append(entry)
        return result
