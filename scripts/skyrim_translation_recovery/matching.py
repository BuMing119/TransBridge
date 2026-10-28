"""Match exported revisions conservatively before considering ANK fallback text."""

from collections import defaultdict
from dataclasses import dataclass

from transbridge.application.io.stage_policy import Stage


def field(key: str) -> str:
    return key.rsplit("~", 1)[-1]


@dataclass(frozen=True)
class RemoteRow:
    reference: str
    plugin: str
    key: str
    original: str
    translation: str
    stage: int
    payload: dict


@dataclass(frozen=True)
class Decision:
    translation: str
    stage: int
    reason: str
    references: tuple[str, ...] = ()


class TranslationIndex:
    def __init__(self, rows: list[RemoteRow]):
        self.exact = defaultdict(list)
        self.content = defaultdict(list)
        for row in rows:
            self.exact[row.plugin, row.key, row.original].append(row)
            if row.original:
                self.content[row.plugin, field(row.key), row.original].append(row)

    def match(self, plugin: str, key: str, original: str) -> Decision | None:
        exact = self.exact.get((plugin, key, original), ())
        # An explicit hidden/locked/translated row is authoritative, even when blank.
        if exact and any(row.translation or row.stage != 0 for row in exact):
            return consensus(exact, "para_exact")
        candidates = self.content.get((plugin, field(key), original), ())
        translated = [row for row in candidates if row.translation and row.stage != -1]
        if translated:
            return consensus(translated, "para_original")
        if exact:
            return consensus(exact, "para_empty")
        return None


def consensus(rows, reason: str) -> Decision:
    states = {(row.translation, row.stage) for row in rows}
    refs = tuple(sorted(row.reference for row in rows))
    if len(states) != 1:
        return Decision("", 0, "para_conflict", refs)
    text, stage = next(iter(states))
    return Decision(text, stage, reason, refs)


def recover(index: TranslationIndex, plugin: str, key: str, original: str, ank: str | None) -> Decision:
    remote = index.match(plugin, key, original)
    if remote is not None and (remote.translation or remote.stage != 0 or remote.reason == "para_conflict"):
        return remote
    if original and ank and ank != original:
        return Decision(ank, Stage.TRANSLATED.value, "ank", remote.references if remote else ())
    return remote or Decision("", 0, "untranslated")
