import pytest

from tests.integration.bootstrap.test_terminology_build_review import _Harness
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
        context = app.context("operator", project_id="project-1", variant_id="main")
        services = TerminologyUiServices.from_runtime(app, context)
        harness = _Harness(services.queries, monkeypatch)
        harness.build()
        version = harness.publish()
        profiles = composition.profile_service_for("project-1")
        profile = profiles.create_with_content(
            "project-1",
            "当前方案",
            TerminologyProfileContent(
                mappings=(
                    ProfileTermMapping("Dragon", "巨龙"),
                    ProfileTermMapping("Missing", "孤立映射"),
                )
            ),
        )
        profiles.publish(profile.profile_id, expected_draft_revision=0)
        profiles.select("project-1", "main", profile.profile_id)
        presenter = TerminologyPresenter(services, context)
        query = TermBrowseQuery("project-1", "main")

        effective = presenter.page_loader("list_effective_terms")(query)
        assert [(item.original, item.translation) for item in effective.items] == [("Dragon", "巨龙")]
        assert presenter.page_loader("list_browsable_draft_terms")(query).total == 0
        description = services.browsing.describe(query)
        assert description.base_version_id == version.ref.version_id
        assert description.profile_id == profile.profile_id
        assert {row.state for row in description.mapping_rows} == {
            ProfileMappingState.EFFECTIVE,
            ProfileMappingState.BASE_MISSING,
        }

        draft = harness.build()
        harness.commands.apply_decision(
            "change_translation", harness.context, term_id=draft.decisions[0].term_id, translation="草稿新译名"
        )
        searched = TermBrowseQuery("project-1", "main", TermFilter(search="草稿新译名"))
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
