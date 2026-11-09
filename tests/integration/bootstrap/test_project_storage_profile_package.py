import json
from zipfile import ZipFile

from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectProvisioningRequest
from transbridge.application.terminology_profiles import ProfileTermMapping, TerminologyProfileContent
from transbridge.bootstrap import build_runtime
from transbridge.persistence.project_store.repository import ProjectStore


def _runtime(root):
    return build_runtime({
        "persistence_v2_root": root / "data",
        "ui_config_path": root / "config.toml",
        "translation_memory_root": root / "memory",
    })


def test_package_carries_only_selected_immutable_profile_and_rebases_copy_ownership(tmp_path):
    package = tmp_path / "profile.transbridge"
    context = RequestContext("gui", run_id="portable-profile")
    runtime = _runtime(tmp_path / "origin")
    try:
        use_cases = runtime.use_cases
        commands = use_cases.resolve("gui_project_commands")
        created = commands.create_project(ProjectProvisioningRequest("Portable profile"), context)
        assert created.is_success, created.diagnostics
        project_id = created.value["project_id"]
        factory = use_cases.resolve("terminology_profile_service_factory")
        profiles = factory.profile_service_for(project_id)
        selected_content = TerminologyProfileContent((ProfileTermMapping("Sword", "剑"),))
        profile = profiles.create_with_content(project_id, "Selected scheme", selected_content)
        selected = profiles.publish(profile.profile_id, expected_draft_revision=0)
        created_variant = commands.save_profile_variant(
            "Named variant", context, target_profile=selected, source_profile=None
        )
        assert created_variant.is_success, created_variant.diagnostics
        variant_id = created_variant.value["id"]
        assert profiles.selected_revision(project_id, variant_id) == selected

        later_content = TerminologyProfileContent((ProfileTermMapping("Sword", "后续修订"),))
        later_draft = profiles.save_draft(profile.profile_id, later_content, expected_revision=0)
        unselected_later = profiles.publish(profile.profile_id, expected_draft_revision=later_draft.draft_revision)
        other = profiles.create_with_content(project_id, "Unselected scheme", later_content)
        profiles.publish(other.profile_id, expected_draft_revision=0)
        assert profiles.selected_revision(project_id, variant_id) == selected
        use_cases.resolve("project_archive").export_project(str(package), context)
    finally:
        assert runtime.close().is_success

    with ZipFile(package) as archive:
        assets = json.loads(archive.read("terminology-assets.json"))
    assert [(item["profile_id"], item["revision"]) for item in assets["profiles"]] == [
        (selected.profile_id, selected.revision)
    ]

    destination = _runtime(tmp_path / "destination")
    try:
        archive = destination.use_cases.resolve("project_archive")
        installed = archive.import_project(str(package), context)
        copied = archive.import_project(
            str(package), context, requested_name="Portable copy", copy_on_identity_conflict=True
        )
        installed_summary = ProjectStore(installed).summary()
        copied_summary = ProjectStore(copied).summary()
        assert installed_summary.identity.value == project_id and copied_summary.identity.value != project_id
        assert installed_summary.active_variant.value == variant_id
        assert copied_summary.active_variant.value != variant_id
        factory = destination.use_cases.resolve("terminology_profile_service_factory")
        for summary in (installed_summary, copied_summary):
            identity = summary.identity.value
            profiles = factory.profile_service_for(identity)
            revision = profiles.selected_revision(identity, summary.active_variant.value)
            assert revision is not None
            assert revision.project_id == identity
            assert (revision.profile_id, revision.revision, revision.content) == (
                selected.profile_id,
                selected.revision,
                selected_content,
            )
            assert profiles.published_revision(profile.profile_id, unselected_later.revision) is None
            assert profiles.published_revision(other.profile_id, 1) is None
    finally:
        assert destination.close().is_success
