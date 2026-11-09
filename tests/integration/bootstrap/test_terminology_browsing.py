import pytest

from tests.integration.bootstrap.test_terminology_build_review import _Harness
from transbridge.application.projects import ProjectProvisioningRequest
from transbridge.application.terminology.browse_queries import TermBrowseQuery, TermFilter
from transbridge.application.terminology_profiles.browse_queries import ProfileMappingState
from transbridge.application.terminology_profiles.models import ProfileTermMapping, TerminologyProfileContent
from transbridge.bootstrap import build_runtime
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices

pytestmark = pytest.mark.integration


def test_production_browsing_uses_sqlite_draft_and_the_ai_profile_composition(tmp_path, monkeypatch):
    app = build_runtime(settings={"persistence_v2_root": tmp_path})
    try:
        composition = app.use_cases.resolve("terminology_ui_services_factory")
        # Browsing has no network dependency; this test does not open a sync workflow.
        monkeypatch.setattr(type(composition), "sync_service_for", lambda *args: None)
        commands = app.use_cases.resolve("gui_project_commands")
        creation_context = app.context("operator")
        project = commands.create_project(ProjectProvisioningRequest("Terminology browsing"), creation_context)
        assert project.is_success, project.diagnostics
        project_id = project.value["project_id"]
        profiles = composition.profile_service_for(project_id)
        profile = profiles.create_with_content(
            project_id,
            "当前方案",
            TerminologyProfileContent(
                mappings=(
                    ProfileTermMapping("Dragon", "巨龙"),
                    ProfileTermMapping("Missing", "孤立映射"),
                )
            ),
        )
        published = profiles.publish(profile.profile_id, expected_draft_revision=0)
        selected = commands.save_profile_variant(
            "Selected terminology", creation_context, target_profile=published, source_profile=None
        )
        assert selected.is_success, selected.diagnostics
        variant_id = selected.value["id"]
        context = app.context("operator", project_id=project_id, variant_id=variant_id)
        services = TerminologyUiServices.from_runtime(app, context)
        harness = _Harness(services.queries, monkeypatch, project_id=project_id, variant_id=variant_id)
        harness.build()
        version = harness.publish()
        presenter = TerminologyPresenter(services, context)
        query = TermBrowseQuery(project_id, variant_id)

        effective = presenter.page_loader("list_effective_terms")(query)
        assert [(item.original, item.translation) for item in effective.items] == [
            ("Dragon", "巨龙"),
            ("Missing", "孤立映射"),
        ]
        assert presenter.page_loader("list_browsable_draft_terms")(query).total == 0
        description = services.browsing.describe(query)
        assert description.base_version_id == version.ref.version_id
        assert description.profile_id == profile.profile_id
        # A saved v2 association authorizes the complete profile, including
        # mappings that do not occur in the independently published base.
        assert {row.state for row in description.mapping_rows} == {ProfileMappingState.EFFECTIVE}

        draft = harness.build()
        harness.commands.apply_decision(
            "change_translation", harness.context, term_id=draft.decisions[0].term_id, translation="草稿新译名"
        )
        searched = TermBrowseQuery(project_id, variant_id, TermFilter(search="草稿新译名"))
        assert presenter.page_loader("list_browsable_draft_terms")(searched).total == 1
        assert presenter.page_loader("list_effective_terms")(searched).total == 0
        assert presenter.page_loader("list_effective_terms")(query) == effective
        presenter.close()
    finally:
        app.close()


def test_old_services_positional_construction_remains_compatible():
    sync = object()
    services = TerminologyUiServices(None, None, None, None, sync)
    assert services.sync is sync
    assert services.browsing is None
