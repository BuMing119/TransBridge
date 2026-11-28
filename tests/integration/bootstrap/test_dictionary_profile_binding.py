from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.contracts.io.test_translation_import_policy import target_and_source
from tests.plugin_fixtures import write_plugin
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.dictionary_apply import dictionary_library, prepare_library_application
from transbridge.application.terminology_profiles import ProfileTermMapping, TerminologyProfileContent
from transbridge.bootstrap import build_runtime
from transbridge.config.text_encoding import TextEncodings
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.terminology.project_selection import ProjectTerminologySelection
from transbridge.persistence.v2.ids import ProjectId
from transbridge.translation_memory.library import DictionaryLibrary


def _runtime(root):
    return build_runtime({
        "persistence_v2_root": root / "data",
        "ui_config_path": root / "transbridge.ini",
        "translation_memory_root": root / "dictionaries",
    })


def _published_variant(runtime, request, project_id):
    factory = runtime.use_cases.resolve("terminology_profile_service_factory")
    profiles = factory.profile_service_for(project_id)
    scheme = profiles.create_with_content(
        project_id,
        "正式方案",
        TerminologyProfileContent((
            ProfileTermMapping("Hello", "方案词", "默认词", scheme_owned=True),
            ProfileTermMapping("Guard", "方案守卫", "默认守卫", "plugin", "Plugin.esp", scheme_owned=True),
        )),
    )
    published = profiles.publish(scheme.profile_id, expected_draft_revision=0)
    commands = runtime.use_cases.resolve("gui_project_commands")
    created = commands.save_profile_variant("独立正文", request, target_profile=published, source_profile=None)
    assert created.is_success, created.diagnostics
    return created.value["id"], profiles, published


def test_dictionary_reads_the_exact_formal_variant_in_the_worker(tmp_path):
    with closing(_runtime(tmp_path)) as runtime:
        commands = runtime.use_cases.resolve("gui_project_commands")
        request = runtime.context("test")
        opened = commands.create_project(ProjectProvisioningRequest("默认工程"), request)
        assert opened.is_success, opened.diagnostics
        project_id, default_variant = opened.value["project_id"], opened.value["variant_id"]
        factory = runtime.use_cases.resolve("terminology_profile_service_factory")
        source = SimpleNamespace(
            active_version_identity=(project_id, default_variant), terminology_profile_service_factory=factory
        )
        default_library = dictionary_library(source, tmp_path / "dictionaries", encodings=TextEncodings())
        assert default_library.text_projection(to_default=False) is None
        assert not factory.repositories.paths.database(project_id).exists()
        entry = TranslationEntry("row", "row", "Hello", "", 0, None)
        guard = TranslationEntry("guard", "guard", "Guard", "", 0, None)
        assert (
            default_library.save(
                [replace(entry, translation="默认词", stage=1), replace(guard, translation="默认守卫", stage=1)],
                "Plugin",
            )
            == 2
        )

        scheme_variant, profiles, published = _published_variant(runtime, request, project_id)
        source.active_version_identity = (project_id, scheme_variant)
        scheme_library = dictionary_library(source, tmp_path / "dictionaries", encodings=TextEncodings())
        source.active_version_identity = (project_id, default_variant)
        current = profiles.save_draft(
            published.profile_id,
            TerminologyProfileContent((ProfileTermMapping("Hello", "新发布", "默认词", scheme_owned=True),)),
            expected_revision=0,
        )
        profiles.publish(published.profile_id, expected_draft_revision=current.draft_revision)
        with ThreadPoolExecutor(max_workers=1) as worker:
            result = worker.submit(
                prepare_library_application,
                [entry, guard],
                scheme_library,
                mod_file_id="Plugin",
                plugin_scope="Plugin.esp",
            ).result()
        assert result.states == {entry.identity: ("方案词", 0), guard.identity: ("方案守卫", 0)}

        # A missing revision referenced by the formal row must block dictionary operations.
        project = runtime.use_cases.resolve("persistence_v2").projects.store_for(ProjectId(project_id))
        selected = profiles.selection(project_id, scheme_variant)
        with project_connection(project.path, write=True) as db:
            db.execute(
                "UPDATE variant SET terminology_selection = ? WHERE identity = ?",
                (ProjectTerminologySelection.encode(replace(selected, revision=999)).decode(), scheme_variant),
            )
        with pytest.raises(RuntimeError, match="正文译名方案"):
            scheme_library.text_projection(to_default=True)


@pytest.mark.parametrize(
    "automatic, explicit, expected", [(True, False, "Wörterbuch"), (False, False, ""), (True, True, "你好")]
)
def test_new_plugin_uses_new_project_language_and_default_dictionary(tmp_path, automatic, explicit, expected):
    with closing(_runtime(tmp_path)) as runtime:
        uses = runtime.use_cases
        commands = uses.resolve("gui_project_commands")
        request = runtime.context("test")
        old = commands.create_project(ProjectProvisioningRequest("旧工程"), request)
        assert old.is_success, old.diagnostics
        old_id = old.value["project_id"]
        old_variant, _, _ = _published_variant(runtime, request, old_id)
        request = replace(request, project_id=old_id, variant_id=old_variant)
        uses.resolve("text_encoding_defaults").save(TextEncodings(target_language="german"))
        uses.resolve("dictionary_preferences").set_apply_on_plugin_open(automatic)
        seed = TranslationEntry("seed", "seed", "Hello", "默认词", 1, None)
        library_root = tmp_path / "dictionaries"
        assert DictionaryLibrary(library_root).save([seed], "Other") == 1
        german = DictionaryLibrary(library_root, target_locale="de")
        assert german.save([replace(seed, translation="Wörterbuch")], "Other") == 1
        if explicit:
            _, donor = target_and_source(tmp_path, "xml.eet")
            path = tmp_path / "Test.esp"
            options = (("initial_translation", {"location": str(donor)}),)
        else:
            path = write_plugin(tmp_path / "New.esp", [(0x800, "Record", "Hello")])
            options = ()
        created = commands.create_project(
            ProjectProvisioningRequest("新工程", source=ProjectSourceRequest(str(path), options=options)), request
        )
        assert created.is_success, created.diagnostics
        assert created.value["project_id"] != old_id
        active = uses.resolve("project_lifecycle").active
        (row,) = active.variant.snapshot().entries
        assert (row.translation, row.stage.value) == (expected, int(explicit))
        path.unlink()
        reopened = uses.resolve("current_project_opener").prepare_active(runtime.context("test"))
        assert reopened.is_success and reopened.value.recovery is None
        assert [(row.translation, row.stage) for row in reopened.value.hydrations[0].entries] == [
            (expected, int(explicit))
        ]
