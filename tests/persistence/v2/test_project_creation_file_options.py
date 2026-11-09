"""Actual guided creation uses native parsers and one atomic project publication."""

import json
from uuid import uuid4

import pytest

from tests.contracts.io.test_open_translation_file import FORMATS, source_file
from tests.contracts.io.test_translation_import_policy import target_and_source
from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.ui.coordinators.guided_project_coordinator import GuidedDraftPhase, GuidedProjectCoordinator


@pytest.fixture
def creation(tmp_path):
    services = build_persistence_v2_services(
        tmp_path / "data", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    context = RequestContext("gui", run_id=uuid4().hex)
    coordinator = GuidedProjectCoordinator(services.gui_project_commands, context)
    yield services, coordinator, context
    services.close()


@pytest.mark.parametrize("kind", FORMATS)
@pytest.mark.parametrize("explicit", [False, True])
def test_every_source_format_creates_and_restores_without_input(creation, tmp_path, kind, explicit):
    services, coordinator, context = creation
    path = source_file(tmp_path, kind)
    coordinator.begin(str(path))
    coordinator.set_source_format(kind if explicit else None)
    assert coordinator.create()
    assert coordinator.state.phase is GuidedDraftPhase.COMPLETED, coordinator.state.diagnostic_message
    before = services.project_lifecycle.active.variant.snapshot().entries
    assert len(before) == 1
    assert services.gui_project_commands.save(context).is_success
    path.unlink()
    opened = services.current_project_opener.prepare_active(context)
    assert opened.is_success and opened.value.recovery is None, opened.diagnostics
    assert len(opened.value.hydrations) == 1
    assert opened.value.hydrations[0].format_id.value == kind
    assert [e.translation for e in opened.value.hydrations[0].entries] == [e.translation for e in before]


@pytest.mark.parametrize("kind", FORMATS)
def test_donor_folds_into_source_without_registering_extra_content(creation, tmp_path, kind):
    services, coordinator, context = creation
    _, donor = target_and_source(tmp_path, kind)
    path = tmp_path / "Test.esp"
    coordinator.begin(str(path))
    coordinator.set_translation_options(
        enabled=True, path=str(donor), format_hint=kind, replace_existing=False, strings_lang="chinese"
    )
    assert coordinator.create()
    assert coordinator.state.phase is GuidedDraftPhase.COMPLETED, coordinator.state.diagnostic_message
    assert [e.translation for e in services.project_lifecycle.active.variant.snapshot().entries] == ["你好"]
    hydration = services.gui_project_commands.consume_create_hydration(
        services.project_lifecycle.active.project_ref.identity.value, context
    )
    assert hydration.is_success
    assert len(hydration.value.sources) == 1
    assert hydration.value.sources[0].format_id.value == "plugin.sse"
    assert services.gui_project_commands.save(context).is_success
    donor.unlink()
    opened = services.current_project_opener.prepare_active(context)
    assert opened.is_success and opened.value.recovery is None, opened.diagnostics
    assert [e.translation for e in opened.value.hydrations[0].entries] == ["你好"]


@pytest.mark.parametrize("overwrite", [False, True])
def test_native_source_respects_keep_and_replace_on_creation(creation, tmp_path, overwrite):
    from transbridge.parser.eet_binary_codec import EetDocument

    services, coordinator, _ = creation
    path = source_file(tmp_path, "binary.eet")
    document = EetDocument.from_bytes(path.read_bytes())
    path.write_bytes(document.with_translation(0, "已有译文").to_bytes())
    _, donor = target_and_source(tmp_path, "xml.eet")
    coordinator.begin(str(path))
    coordinator.set_translation_options(
        enabled=True, path=str(donor), format_hint=None, replace_existing=overwrite, strings_lang="chinese"
    )
    coordinator.create()
    assert coordinator.state.phase is GuidedDraftPhase.COMPLETED, coordinator.state.diagnostic_message
    assert services.project_lifecycle.active.variant.snapshot().entries[0].translation == (
        "你好" if overwrite else "已有译文"
    )


@pytest.mark.parametrize("failure", ["primary", "donor", "wrong-json", "partial-json"])
def test_invalid_input_never_publishes_partial_project(creation, tmp_path, failure):
    services, coordinator, _ = creation
    good = source_file(tmp_path, "binary.eet")
    bad = tmp_path / "broken.json"
    bad.write_bytes(b"bad")
    coordinator.begin(str(good))
    if failure == "primary":
        coordinator.set_source_paths((str(good), str(bad)))
    elif failure == "donor":
        coordinator.set_translation_options(
            enabled=True, path=str(bad), format_hint=None, replace_existing=False, strings_lang="chinese"
        )
    else:
        path = source_file(tmp_path, "json.paratranz")
        if failure == "partial-json":
            data = json.loads(path.read_text(encoding="utf-8"))
            data.append({"key": "broken"})
            path.write_text(json.dumps(data), encoding="utf-8")
        coordinator.set_source_paths((str(path),))
        coordinator.set_source_format("json.dsd" if failure == "wrong-json" else "json.paratranz")
    coordinator.create()
    assert coordinator.state.phase is GuidedDraftPhase.FAILED
    assert services.project_lifecycle.active is None
    assert coordinator.state.diagnostic_message


def test_preparation_actions_are_not_retained_as_parser_options(tmp_path):
    from transbridge.application.projects import ProjectSourceRequest
    from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer

    _, donor = target_and_source(tmp_path, "xml.eet")
    prepared = TranslationIoProjectSourcePreparer().prepare_source(
        ProjectSourceRequest(
            str(tmp_path / "Test.esp"),
            options=(("initial_translation", {"location": str(donor)}), ("require_complete", True)),
        ),
        RequestContext("test"),
        role="primary",
        common_options=(),
    )
    options = prepared.to_dict()["format_options"]
    assert "initial_translation" not in options
    assert "require_complete" not in options
    assert prepared.hydration.entries[0].translation == "你好"


def test_no_matching_translation_is_reported_after_success(creation, tmp_path):
    _, coordinator, _ = creation
    path = source_file(tmp_path, "binary.eet")
    _, donor = target_and_source(tmp_path, "xml.eet")
    donor.write_text(donor.read_text(encoding="utf-8").replace("Hello", "Other"), encoding="utf-8")
    coordinator.begin(str(path))
    coordinator.set_translation_options(
        enabled=True, path=str(donor), format_hint="xml.eet", replace_existing=True, strings_lang="chinese"
    )
    coordinator.create()
    assert coordinator.state.phase is GuidedDraftPhase.COMPLETED, coordinator.state.diagnostic_message
    assert len(coordinator.state.completion_notes) == 1
    assert "未产生更新" in coordinator.state.completion_notes[0]


def test_strings_directory_applies_to_each_localized_plugin(creation, tmp_path):
    from tests.ui.test_parse_migrations import _localized_plugin, _strings

    services, coordinator, _ = creation
    donor = tmp_path / "Chinese"
    donor.mkdir()
    original_strings = tmp_path / "Strings"
    original_strings.mkdir()
    paths = []
    for index in (1, 2):
        path = tmp_path / f"Source{index}.esp"
        _localized_plugin(path, index)
        _strings(original_strings / f"Source{index}_English.strings", index, f"Source {index}")
        _strings(donor / f"Source{index}_Chinese.strings", index, f"译文 {index}")
        paths.append(str(path))
    coordinator.begin_sources(tuple(paths))
    coordinator.set_translation_options(
        enabled=True, path=str(donor), format_hint="strings-directory", replace_existing=False, strings_lang="chinese"
    )
    coordinator.create()
    assert coordinator.state.phase is GuidedDraftPhase.COMPLETED, coordinator.state.diagnostic_message
    assert sorted(e.translation for e in services.project_lifecycle.active.variant.snapshot().entries) == [
        "译文 1",
        "译文 2",
    ]


def test_strings_directory_rejects_nonplugin_primary(creation, tmp_path):
    services, coordinator, _ = creation
    path = source_file(tmp_path, "binary.eet")
    coordinator.begin(str(path))
    coordinator.set_translation_options(
        enabled=True,
        path=str(tmp_path),
        format_hint="strings-directory",
        replace_existing=False,
        strings_lang="chinese",
    )
    coordinator.create()
    assert coordinator.state.phase is GuidedDraftPhase.FAILED
    assert coordinator.state.diagnostic_code == "INITIAL_STRINGS_PLUGIN_REQUIRED"
    assert services.project_lifecycle.active is None


def test_mixed_donor_batch_creates_one_project_with_two_matched_sources(creation, tmp_path):
    from tests.plugin_fixtures import write_plugin
    from transbridge.parser.plugin_parser import PluginParser

    services, coordinator, _ = creation
    _, sst = target_and_source(tmp_path, "sst.ssu9")
    second = write_plugin(tmp_path / "Second.esp", [(0x801, "OtherGreeting", "Goodbye")])
    donor = tmp_path / "second.json"
    donor.write_text(
        json.dumps([
            {
                "key": PluginParser().parse_plugin(second)[0].key,
                "original": "Goodbye",
                "translation": "再见",
            }
        ]),
        encoding="utf-8",
    )
    coordinator.begin_sources((str(tmp_path / "Test.esp"), str(second)))
    coordinator.set_translation_options(
        enabled=True, paths=(str(sst), str(donor)), format_hint=None, replace_existing=False, strings_lang="chinese"
    )
    coordinator.create()
    assert coordinator.state.phase is GuidedDraftPhase.COMPLETED, coordinator.state.diagnostic_message
    assert sorted(e.translation for e in services.project_lifecycle.active.variant.snapshot().entries) == [
        "你好",
        "再见",
    ]


def test_conflict_in_later_donor_prevents_entire_project_creation(creation, tmp_path):
    services, coordinator, _ = creation
    _, donor = target_and_source(tmp_path, "xml.eet")
    conflict = tmp_path / "conflict.xml"
    conflict.write_text(donor.read_text(encoding="utf-8").replace("你好", "不同译文"), encoding="utf-8")
    coordinator.begin(str(tmp_path / "Test.esp"))
    coordinator.set_translation_options(
        enabled=True,
        paths=(str(donor), str(conflict)),
        format_hint=None,
        replace_existing=False,
        strings_lang="chinese",
    )
    coordinator.create()
    assert coordinator.state.phase is GuidedDraftPhase.FAILED
    assert coordinator.state.diagnostic_code == "INITIAL_TRANSLATION_CONFLICT"
    assert services.project_lifecycle.active is None
