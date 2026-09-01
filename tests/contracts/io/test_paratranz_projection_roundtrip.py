from __future__ import annotations

import json

import pytest

from tests.contracts.io.test_paratranz_adapter import _parse, _write
from transbridge.application.contracts import OperationOutcome
from transbridge.application.io.paratranz_mapping import paratranz_record_from_entry
from transbridge.converter.translation_entry import TranslationEntry


@pytest.mark.parametrize("representation", ["entry", "snapshot", "serialized"])
def test_extensions_survive_project_hydration_and_native_json_write(tmp_path, representation):
    source = tmp_path / "source.json"
    payload = {
        "id": 71,
        "key": "same",
        "original": "Original",
        "translation": "Translated",
        "stage": 1,
        "context": "Context",
        "note": "keep",
        "metadata": {"custom": [1, True, None]},
    }
    source.write_text(json.dumps([payload]), encoding="utf-8")
    parsed = _parse(source)
    assert parsed.outcome is OperationOutcome.COMPLETED
    entry = parsed.entries[0].to_translation_entry()
    if representation == "snapshot":
        entry = entry.snapshot()
    elif representation == "serialized":
        entry = TranslationEntry.from_dict(json.loads(json.dumps(entry.to_dict())))
    target = tmp_path / "out.json"
    result = _write(target, (entry,))
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert json.loads(target.read_text(encoding="utf-8")) == [payload]
    stripped = paratranz_record_from_entry(entry, preserve_extensions=False)
    assert "note" not in stripped and "metadata" not in stripped


def test_unrelated_source_metadata_is_not_exported_as_paratranz_extensions():
    entry = TranslationEntry(
        id="key",
        key="key",
        original="Text",
        translation="",
        stage=0,
        context=None,
        metadata=(("plugin_path", "private"),),
    )
    assert "plugin_path" not in paratranz_record_from_entry(entry.snapshot())
