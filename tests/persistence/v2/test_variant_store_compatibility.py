from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.variant_store import VariantStore


def _entry(key: str, translation: str, stage: int = 1) -> TranslationEntry:
    return TranslationEntry(
        id=key,
        key=key,
        original=f"source-{key}",
        translation=translation,
        stage=stage,
        context=None,
    )


def test_collect_from_is_full_and_preserves_explicit_empty(tmp_path: Path) -> None:
    store = VariantStore(tmp_path / "variant" / "current.json")
    store.translations = {"old": "must-not-resurrect"}
    store.labels = {"old": {"must-not-resurrect"}}

    store.collect_from(
        [_entry("current", "", stage=0)],
        {"current": set()},
        {"review": {"color": "blue"}},
    )

    assert store.translations == {"current": ""}
    assert store.labels == {"current": set()}
    assert store.entry_states["current"]["stage"] == 0
    assert "old" not in store.entry_states


def test_legacy_projection_replaces_missing_values_when_baseline_is_explicit(tmp_path: Path) -> None:
    store = VariantStore(tmp_path / "variant" / "current.json")
    store.translations = {"b": "from-B"}
    store.entry_states = {"b": {"stage": 3}}
    runtime = [_entry("a", "from-A"), _entry("b", "from-A")]
    baseline = [_entry("a", "", 0), _entry("b", "", 0)]

    with pytest.warns(DeprecationWarning):
        updated = store.apply_to(runtime, source_baseline=deepcopy(baseline))

    assert updated == 2
    assert [(entry.translation, entry.stage) for entry in runtime] == [("", 0), ("from-B", 3)]


def test_legacy_projection_without_baseline_exposes_lossy_boundary(tmp_path: Path) -> None:
    store = VariantStore(tmp_path / "variant" / "current.json")
    runtime = [_entry("a", "from-A")]

    with pytest.warns(DeprecationWarning, match="lossy compatibility path"):
        updated = store.apply_to(runtime)

    assert updated == 0
    assert runtime[0].translation == "from-A"
