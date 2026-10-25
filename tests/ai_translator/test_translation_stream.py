import json
import re

import pytest

from transbridge.ai_translator.translation_stream import TranslationStream


def legacy_extract(text):
    """Frozen pre-change oracle, including permissive separators and duplicates."""
    match = re.search(r'"results"\s*:\s*\[', text)
    if not match:
        return {}
    index = match.end()
    decoder = json.JSONDecoder()
    result, duplicates = {}, set()
    while index < len(text):
        while index < len(text) and text[index] in " \t\r\n,":
            index += 1
        if index >= len(text) or text[index] == "]":
            break
        try:
            item, index = decoder.raw_decode(text, index)
        except ValueError:
            break
        if not isinstance(item, dict) or set(item) != {"entry_id", "translation"}:
            continue
        key, value = item["entry_id"], item["translation"]
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            continue
        if key in result:
            duplicates.add(key)
        else:
            result[key] = value
    return {key: value for key, value in result.items() if key not in duplicates}


ITEMS = [
    {"entry_id": "a", "translation": '译文\\"\n\t\u263a😀'},
    {"entry_id": "b", "translation": "second"},
    {"entry_id": "a", "translation": "duplicate"},
    {"entry_id": "a", "translation": "third"},
    {"entry_id": "ignored", "translation": "", "extra": []},
    {"entry_id": 123, "translation": "invalid"},
    {"entry_id": "", "translation": "legacy permits empty ID"},
]


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 16, 123, 10000])
@pytest.mark.parametrize("prefix", ['{"results":[', '```json\n{"results" \n :\t [', 'x"result""results":['])
def test_every_prefix_matches_legacy_and_delta_reconstructs_state(chunk_size, prefix):
    text = prefix + ",".join(json.dumps(item, ensure_ascii=True) for item in [None, 1, [], *ITEMS]) + "]}"
    parser, displayed = TranslationStream(), {}
    for offset in range(0, len(text), chunk_size):
        chunk = text[offset : offset + chunk_size]
        delta = parser.feed(chunk)
        displayed.update(delta.added)
        for key in delta.invalidated:
            displayed.pop(key, None)
        assert displayed == parser.translations == legacy_extract(text[: offset + chunk_size])


@pytest.mark.parametrize("bad", ['{"bad":oops}', '{"entry_id":"bad","translation":"\\q"}', "oops", "[}]", "truefalse"])
def test_malformed_item_prevents_later_salvage(bad):
    text = '{"results":[' + json.dumps(ITEMS[0]) + "," + bad + "," + json.dumps(ITEMS[1]) + "]}"
    parser = TranslationStream()
    for char in text:
        parser.feed(char)
    assert parser.translations == legacy_extract(text)


def test_completed_items_are_decoded_once(monkeypatch):
    actual = json.loads
    calls = []

    def decode(text):
        calls.append(text)
        return actual(text)

    monkeypatch.setattr(json, "loads", decode)
    parser = TranslationStream()
    text = json.dumps({"results": ITEMS})
    for char in text:
        parser.feed(char)
    assert len(calls) == len(ITEMS)


def test_incomplete_huge_string_is_not_redecoded(monkeypatch):
    monkeypatch.setattr(json, "loads", lambda _: pytest.fail("incomplete value decoded"))
    parser = TranslationStream()
    parser.feed('{"results":[{"entry_id":"a","translation":"')
    for _ in range(1000):
        parser.feed('long text\\"\u263a')
    assert parser.translations == {}
