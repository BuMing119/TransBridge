"""Per-request extraction of completed results without re-decoding history."""

from __future__ import annotations

from dataclasses import dataclass, field
import json


@dataclass(slots=True)
class TranslationDelta:
    added: dict[str, str] = field(default_factory=dict)
    invalidated: set[str] = field(default_factory=set)


class TranslationStream:
    """Extract partial items; final response validation remains authoritative."""

    def __init__(self) -> None:
        self.translations: dict[str, str] = {}
        self._duplicates: set[str] = set()
        self._header = 0
        self._active = False
        self._done = False
        self._parts: list[str] = []
        self._depth = 0
        self._quoted = False
        self._escaped = False
        self._primitive = False

    def feed(self, chunk: str) -> TranslationDelta:
        delta = TranslationDelta()
        for char in chunk:
            if self._done:
                break
            if not self._active:
                self._find_header(char)
                continue
            if self._primitive and char in ' \t\r\n,]}{["':
                self._finish(delta)
                if self._done:
                    break
            if not self._parts:
                if char in " \t\r\n,":
                    continue
                if char == "]":
                    self._done = True
                    break
                self._primitive = char not in '{["'
            self._parts.append(char)
            if self._primitive:
                continue
            if self._quoted:
                if self._escaped:
                    self._escaped = False
                elif char == "\\":
                    self._escaped = True
                elif char == '"':
                    self._quoted = False
            elif char == '"':
                self._quoted = True
            elif char in "{[":
                self._depth += 1
            elif char in "}]":
                self._depth -= 1
            if self._depth <= 0 and not self._quoted:
                self._finish(delta)
        return delta

    def _find_header(self, char: str) -> None:
        literal = '"results"'
        index = self._header
        if index < len(literal) and char == literal[index]:
            self._header += 1
        elif index >= len(literal) and char.isspace():
            pass
        elif index == len(literal) and char == ":":
            self._header += 1
        elif index == len(literal) + 1 and char == "[":
            self._active = True
        else:
            self._header = 1 if char == '"' else 0

    def _finish(self, delta: TranslationDelta) -> None:
        try:
            text = "".join(self._parts)
            if self._primitive:
                decoder = json.JSONDecoder()
                index = 0
                while index < len(text):
                    _, index = decoder.raw_decode(text, index)
                item = None
            else:
                item = json.loads(text)
        except (ValueError, RecursionError):
            self._done = True
            return
        self._parts.clear()
        self._primitive = False
        if not isinstance(item, dict) or set(item) != {"entry_id", "translation"}:
            return
        entry_id, translation = item["entry_id"], item["translation"]
        if not isinstance(entry_id, str) or not isinstance(translation, str) or not translation:
            return
        if entry_id in self.translations or entry_id in self._duplicates:
            self._duplicates.add(entry_id)
            self.translations.pop(entry_id, None)
            delta.added.pop(entry_id, None)
            delta.invalidated.add(entry_id)
        else:
            self.translations[entry_id] = translation
            delta.added[entry_id] = translation
