"""Versioned plugin filenames resolve without changing source ownership."""

import pytest

from transbridge.paratranz.download_paths import (
    legacy_plugin_paths,
    select_download_files,
    unambiguous_legacy_paths,
)


@pytest.mark.parametrize("extension", ["esp", "esm", "esl", "ESM"])
def test_legacy_names_remove_only_plugin_suffix_from_first_component(extension):
    assert legacy_plugin_paths((f"Base.{extension}.json", f"Base.{extension}/任务/A.esp.json")) == (
        "Base.json",
        "Base/任务/A.esp.json",
    )


def test_non_plugin_extensions_and_existing_source_disambiguation_are_preserved():
    assert legacy_plugin_paths(("Base.xml.json", "Base.json", "Base.esp_abcdef1234.json")) == ("Base_abcdef1234.json",)


@pytest.mark.parametrize("preferred", ["Base.esm.json", "Base.esm/人名.json", "Base.esm_1.json"])
def test_any_current_layout_match_excludes_all_old_layout_files(preferred):
    current = ("Base.esm.json", "Base.esm/人名.json")
    inventory = {"Base.json": 1, "Base/人名.json": 2, preferred: 3}
    assert select_download_files(inventory, current, legacy_plugin_paths(current)) == ((preferred, 3),)


def test_legacy_layout_keeps_the_exact_category_and_numeric_partition_whitelist():
    current = ("Base.esm.json", "Base.esm/任务/当前.json")
    inventory = {
        "Base_1.json": 1,
        "Base/任务/当前_2.json": 2,
        "Base/任务/旧名.json": 3,
        "Elsewhere/Base.json": 4,
        "Base/人名.json": 5,
        "Base_0.json": 6,
        "Base.json.bak": 7,
    }
    assert select_download_files(inventory, current, legacy_plugin_paths(current)) == (
        ("Base_1.json", 1),
        ("Base/任务/当前_2.json", 2),
    )


def test_unique_case_insensitive_match_preserves_actual_inventory_spelling():
    assert select_download_files({"base.json": 1}, ("Base.esm.json",), ("Base.json",)) == (("base.json", 1),)


def test_exact_spelling_is_preferred_over_a_case_variant():
    assert select_download_files({"Base.esm.json": 1, "base.esm.json": 2}, ("Base.esm.json",)) == (
        ("Base.esm.json", 1),
    )


def test_two_case_variants_without_an_exact_match_are_not_guessed():
    with pytest.raises(ValueError, match="大小写"):
        select_download_files({"BASE.json": 1, "base.json": 2}, ("Base.esm.json",), ("Base.json",))


@pytest.mark.parametrize("other", ["Base.esm.json", "Base.json", "Base_1.esm.json", "Base_1.json"])
def test_global_source_roots_exclude_ambiguous_legacy_names_even_for_an_unselected_source(other):
    names = {"selected": ("Base.esp.json",), "other": (other,)}
    assert unambiguous_legacy_paths(names)["selected"] == ()


def test_unrelated_sources_do_not_disable_valid_legacy_paths():
    names = {"selected": ("Base.esp.json", "Base.esp/人名.json"), "other": ("Other.esm.json",)}
    assert unambiguous_legacy_paths(names)["selected"] == ("Base.json", "Base/人名.json")


def test_ambiguous_old_file_is_reported_without_blocking_an_available_current_file():
    with pytest.raises(ValueError, match="旧版路径同时对应多个插件"):
        select_download_files({"Base.json": 1}, ("Base.esm.json",), blocked_legacy_paths=("Base.json",))
    assert select_download_files(
        {"Base.json": 1, "Base.esm.json": 2}, ("Base.esm.json",), blocked_legacy_paths=("Base.json",)
    ) == (("Base.esm.json", 2),)
