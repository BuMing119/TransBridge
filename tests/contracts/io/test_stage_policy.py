from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from transbridge.ai_translator.translator import (
    _select_post_process_candidates,
    _select_stage_candidates,
)
from transbridge.application.contracts import DiagnosticSeverity
from transbridge.application.io import (
    DEFAULT_STAGE_POLICY,
    Stage,
    StageOperation,
)
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.fomod.pipeline import _select_fomod_ai_entry_ids
from transbridge.parser.eet_parser import EET_XmlParser
from transbridge.translation_memory.manager import TranslationMemoryManager
from transbridge.writer.eet_xml_writer import EETWriter

FIXTURES = Path(__file__).with_name("fixtures")


def _entry(key: str, stage: int, translation: str = "") -> TranslationEntry:
    return TranslationEntry(key, key, f"original-{key}", translation, stage, "INFO:NAM1")


@pytest.mark.parametrize(
    ("stage", "include_ai", "tm_read", "tm_write", "publish_text"),
    [
        (-1, False, False, False, "original"),
        (0, True, True, False, "original"),
        (1, True, True, True, "translated"),
        (2, True, True, True, "translated"),
        (3, True, True, True, "translated"),
        (5, True, True, True, "translated"),
        (9, False, False, False, "translated"),
    ],
)
def test_seven_stage_policy_matrix_is_explicit(
    stage: int,
    include_ai: bool,
    tm_read: bool,
    tm_write: bool,
    publish_text: str,
) -> None:
    policy = DEFAULT_STAGE_POLICY

    assert policy.evaluate(stage, "translated", StageOperation.AI, original="original").include_ai is include_ai
    assert policy.evaluate(stage, "translated", StageOperation.TM_READ, original="original").include_tm is tm_read
    assert policy.evaluate(stage, "translated", StageOperation.TM_WRITE, original="original").include_tm is tm_write
    assert (
        policy.evaluate(
            stage,
            "translated",
            StageOperation.PUBLISH,
            original="original",
        ).publish_text
        == publish_text
    )


def test_stage_enum_does_not_support_ordinal_comparison() -> None:
    with pytest.raises(TypeError):
        _ = Stage.TRANSLATED < Stage.CHECKED


def test_hidden_always_projects_original_text() -> None:
    preview = DEFAULT_STAGE_POLICY.evaluate(-1, "translated", StageOperation.PREVIEW, original="original")
    publish = DEFAULT_STAGE_POLICY.evaluate(-1, "translated", StageOperation.PUBLISH, original="original")

    assert preview.preview_text == "original"
    assert publish.publish_text == "original"
    assert not preview.include_ai and not preview.include_tm


def test_untranslated_preview_and_publish_project_original_even_with_stale_text() -> None:
    preview = DEFAULT_STAGE_POLICY.evaluate(0, "stale", StageOperation.PREVIEW, original="original")
    publish = DEFAULT_STAGE_POLICY.evaluate(0, "stale", StageOperation.PUBLISH, original="original")

    assert preview.preview_text == "original"
    assert publish.publish_text == "original"
    assert not preview.blocks_publish and not publish.blocks_publish


def test_locked_empty_preview_is_visible_but_formal_publish_is_blocked() -> None:
    preview = DEFAULT_STAGE_POLICY.evaluate(9, "", StageOperation.PREVIEW, original="original")
    publish = DEFAULT_STAGE_POLICY.evaluate(9, "", StageOperation.PUBLISH, original="original")

    assert preview.preview_text == "original"
    assert preview.blocks_publish
    assert preview.code == "STAGE_LOCKED_TRANSLATION_REQUIRED"
    assert preview.severity is DiagnosticSeverity.ERROR
    assert publish.publish_text is None
    assert publish.blocks_publish


@pytest.mark.parametrize("invalid", [4, 6, 7, 8, 10, True, "1"])
def test_unknown_stage_is_never_treated_as_translated(invalid) -> None:
    decision = DEFAULT_STAGE_POLICY.evaluate(invalid, "translated", StageOperation.PUBLISH, original="original")

    assert decision.stage is None
    assert decision.publish_text is None
    assert decision.code == "STAGE_INVALID"
    assert decision.blocks_publish
    assert not decision.include_ai and not decision.include_tm


def test_ai_overwrite_targeting_cannot_reintroduce_hidden_or_locked_entries() -> None:
    entries = [
        _entry("untranslated", 0),
        _entry("translated", 1, "done"),
        _entry("hidden-empty", -1),
        _entry("hidden-text", -1, "hidden"),
        _entry("locked-empty", 9),
        _entry("locked-text", 9, "locked"),
    ]

    default_targets = _select_stage_candidates(entries, overwrite=False)
    overwrite_targets = _select_stage_candidates(entries, overwrite=True)

    assert [entry.key for entry in default_targets] == ["untranslated"]
    assert [entry.key for entry in overwrite_targets] == ["untranslated", "translated"]


def test_post_process_cannot_reintroduce_hidden_or_locked_entries() -> None:
    entries = [
        _entry("translated", 1, "done"),
        _entry("questionable", 2, "review"),
        _entry("hidden", -1, "hidden"),
        _entry("locked", 9, "locked"),
    ]

    assert [entry.key for entry in _select_post_process_candidates(entries, None)] == ["translated", "questionable"]
    assert _select_post_process_candidates(entries, []) == []
    assert [entry.key for entry in _select_post_process_candidates(entries, ["questionable", "hidden", "locked"])] == [
        "questionable"
    ]


def test_fomod_ai_targeting_uses_same_stage_policy() -> None:
    collection = TranslationEntryCollection([
        _entry("untranslated", 0),
        _entry("translated", 1, "done"),
        _entry("hidden-empty", -1),
        _entry("locked-empty", 9),
    ])

    assert _select_fomod_ai_entry_ids(collection) == ["untranslated"]


def test_translation_memory_uses_tm_read_and_write_policy() -> None:
    manager = TranslationMemoryManager()
    source = TranslationEntryCollection([
        _entry("translated", 1, "translated"),
        _entry("questionable", 2, "questionable"),
        _entry("hidden", -1, "hidden"),
        _entry("locked", 9, "locked"),
        _entry("untranslated-with-text", 0, "inconsistent"),
    ])

    added = manager.save_from_collection(source, mod_file_id="stage-policy")

    assert added == 2
    assert set(manager.dictionaries["stage-policy"].key_index) == {"translated", "questionable"}


def test_eet_writer_uses_discrete_preview_publish_policy(tmp_path) -> None:
    source = tmp_path / "source.xml"
    source.write_bytes((FIXTURES / "eet-small.xml").read_bytes())
    parser = EET_XmlParser.from_file(source)
    base = TranslationEntry.create_from_eet_entry(parser.entries[0])
    hidden = replace(base, translation="must-not-publish", stage=-1)

    writer = EETWriter(parser)
    assert writer.apply_collection(TranslationEntryCollection((hidden,))) == 1
    node = parser._tree.getroot().find(".//ESP")
    assert node.findtext("TRADUIT") == hidden.original
    assert node.findtext("STATUS") == "0"

    locked = replace(base, translation="", stage=9)
    with pytest.raises(ValueError, match="STAGE_LOCKED_TRANSLATION_REQUIRED"):
        EETWriter(EET_XmlParser.from_file(source)).apply_collection(TranslationEntryCollection((locked,)))

    locked_same_text = replace(base, translation=base.original, stage=9)
    locked_parser = EET_XmlParser.from_file(source)
    assert EETWriter(locked_parser).apply_collection(TranslationEntryCollection((locked_same_text,))) == 1
    locked_node = locked_parser._tree.getroot().find(".//ESP")
    assert locked_node.findtext("TRADUIT") == base.original
    assert locked_node.findtext("STATUS") == "99"
