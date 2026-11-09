"""Draft edits preserve user choices and never register a new donor as a source."""

from tests.ui.test_start_center_guided_project import _Commands
from transbridge.ui.coordinators.guided_project_coordinator import GuidedProjectCoordinator


def test_source_edits_keep_custom_name_variant_and_translation_choices():
    coordinator = GuidedProjectCoordinator(_Commands(), object())
    coordinator.begin_sources(("D:/mods/One.esp", "D:/mods/Two.eet"))
    assert coordinator.state.project_name == "新翻译工程"
    coordinator.set_project_name("我的批量工程")
    coordinator.set_variant_name("汉化")
    coordinator.set_parse_option("skip_empty", True)
    coordinator.set_translation_options(
        enabled=True,
        path="D:/Chinese.json",
        format_hint="json.paratranz",
        replace_existing=True,
        strings_lang="chinese",
    )
    coordinator.set_source_paths(("D:/mods/Two.eet", "D:/mods/Two.eet"))
    assert len(coordinator.state.source_paths) == 1
    assert coordinator.state.project_name == "我的批量工程"
    assert coordinator.state.default_variant_name == "汉化"
    assert coordinator.state.parse_options == (("skip_empty", True),)
    assert coordinator.state.replace_existing
    coordinator.set_source_paths(())
    assert not coordinator.state.can_submit


def test_disabled_donor_is_not_applied_or_registered():
    commands = _Commands()
    coordinator = GuidedProjectCoordinator(commands, object())
    coordinator.begin("D:/mods/One.eet")
    coordinator.set_translation_options(
        enabled=False, path="missing.json", format_hint="json.dsd", replace_existing=True, strings_lang="chinese"
    )
    coordinator.create()
    request = commands.prepared[0][0]
    assert not request.migration_sources
    assert "initial_translation" not in dict(request.source.options)


def test_enabled_donor_requires_a_path_and_strings_requires_language():
    coordinator = GuidedProjectCoordinator(_Commands(), object())
    coordinator.begin("D:/mods/One.esp")
    coordinator.set_translation_options(
        enabled=True, path=None, format_hint=None, replace_existing=False, strings_lang="chinese"
    )
    assert not coordinator.state.can_submit
    coordinator.set_translation_options(
        enabled=True, path="D:/Strings", format_hint="strings-directory", replace_existing=False, strings_lang=""
    )
    assert not coordinator.state.can_submit


def test_in_flight_edits_do_not_change_the_prepared_request():
    pending = []
    coordinator = GuidedProjectCoordinator(_Commands(), object(), dispatch=lambda *args: pending.append(args) or True)
    coordinator.begin("D:/mods/One.eet")
    coordinator.create()
    state = coordinator.state
    assert not coordinator.set_source_format("sst.ssu9")
    assert not coordinator.set_source_paths(("D:/Other.esp",))
    assert not coordinator.set_translation_options(
        enabled=True, path="D:/New.xml", format_hint=None, replace_existing=False, strings_lang="chinese"
    )
    assert coordinator.state is state
