"""Bulk donor matching is independent of order and never hides conflicting text."""

import json

import pytest

from tests.contracts.io.test_translation_import_policy import target_and_source
from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import DomainError, RequestContext
from transbridge.application.projects import ProjectSourceRequest
from transbridge.parser.plugin_parser import PluginParser
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer


def prepare(path, donors, *, overwrite=False):
    return TranslationIoProjectSourcePreparer().prepare_source(
        ProjectSourceRequest(
            str(path),
            options=(("initial_translation", {"locations": tuple(map(str, donors)), "replace_existing": overwrite}),),
        ),
        RequestContext("batch-test"),
        role="primary",
        common_options=(),
    )


def json_donor(path, key, original, translation):
    path.write_text(json.dumps([{"key": key, "original": original, "translation": translation}]), encoding="utf-8")
    return path


@pytest.mark.parametrize("reverse", [False, True])
def test_mixed_donors_match_each_primary_by_identity_and_original(tmp_path, reverse):
    _, sst = target_and_source(tmp_path, "sst.ssu9")
    first = tmp_path / "Test.esp"
    second = write_plugin(tmp_path / "Second.esp", [(0x800, "Greeting", "Goodbye")])
    entry = PluginParser().parse_plugin(second)[0]
    donor = json_donor(tmp_path / "second.json", entry.key, "Goodbye", "再见")
    unrelated = json_donor(tmp_path / "unrelated.json", "unrelated", "Unrelated", "无关")
    paths = [sst, donor, unrelated]
    if reverse:
        paths.reverse()
    assert prepare(first, paths).hydration.entries[0].translation == "你好"
    assert prepare(second, paths).hydration.entries[0].translation == "再见"


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_files_reject_in_either_selection_order(tmp_path, reverse):
    _, donor = target_and_source(tmp_path, "xml.eet")
    conflict = tmp_path / "different.xml"
    conflict.write_text(donor.read_text(encoding="utf-8").replace("你好", "另一译文"), encoding="utf-8")
    paths = [donor, conflict]
    if reverse:
        paths.reverse()
    with pytest.raises(DomainError, match="匹配到不同译文") as error:
        prepare(tmp_path / "Test.esp", paths)
    assert error.value.code == "INITIAL_TRANSLATION_CONFLICT"
    assert str(donor) in error.value.message and str(conflict) in error.value.message


def test_identical_translations_from_different_formats_are_merged_once(tmp_path):
    _, sst = target_and_source(tmp_path, "sst.ssu9")
    _, xml = target_and_source(tmp_path, "xml.eet")
    first = prepare(tmp_path / "Test.esp", [sst])
    repeated = prepare(tmp_path / "Test.esp", [sst, xml, sst])
    assert repeated.hydration.entries == first.hydration.entries


def test_existing_text_agreement_does_not_hide_another_conflicting_donor(tmp_path):
    from tests.contracts.io.test_open_translation_file import source_file

    primary = source_file(tmp_path, "binary.eet")  # contains 你好 already
    _, same = target_and_source(tmp_path, "xml.eet")
    other = tmp_path / "different.xml"
    other.write_text(same.read_text(encoding="utf-8").replace("你好", "另一译文"), encoding="utf-8")
    assert prepare(primary, [same, other]).hydration.entries[0].translation == "你好"
    with pytest.raises(DomainError, match="匹配到不同译文"):
        prepare(primary, [same, other], overwrite=True)


def test_malformed_selected_donor_is_not_treated_as_unmatched(tmp_path):
    _, good = target_and_source(tmp_path, "xml.eet")
    broken = tmp_path / "broken.eet"
    broken.write_bytes(b"EET_")
    with pytest.raises(DomainError) as error:
        prepare(tmp_path / "Test.esp", [good, broken])
    assert str(broken) in error.value.message


def test_same_json_key_with_changed_original_is_not_applied(tmp_path):
    target, _ = target_and_source(tmp_path, "json.paratranz")
    donor = json_donor(tmp_path / "foreign.json", next(iter(target)).key, "Other original", "错误译文")
    result = prepare(tmp_path / "Test.esp", [donor])
    assert result.hydration.entries[0].translation == ""
    assert result.diagnostics[-1].code == "INITIAL_TRANSLATION_NO_UPDATES"
