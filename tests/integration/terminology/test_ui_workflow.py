from __future__ import annotations

from types import SimpleNamespace

import pytest

from transbridge.application.contracts import JobRef, OperationResult, RequestContext
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.view_models import TERMINOLOGY_AREAS

pytestmark = pytest.mark.integration


class _Inputs:
    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot

    def capture_build_input(self, context, *, config):
        assert (context.project_id, context.variant_id) == ("project", "variant")
        return OperationResult.completed(self.snapshot)


class _Commands:
    def __init__(self) -> None:
        self.calls = []

    def start_build(self, snapshot, context):
        self.calls.append(("build", snapshot.project_id, context.variant_id))
        return JobRef("build-job", context.owner_id, "build-run")

    def publish(self, context):
        self.calls.append(("publish", context.project_id, context.variant_id))
        return JobRef("publish-job", context.owner_id, "publish-run")

    def render_report(self, context):
        self.calls.append(("report", context.project_id, context.variant_id))
        return JobRef("report-job", context.owner_id, "report-run")

    def render_changelog(self, context):
        self.calls.append(("changelog", context.project_id, context.variant_id))
        return JobRef("changelog-job", context.owner_id, "changelog-run")


def test_ui_areas_stay_object_oriented_and_delegate_business_commands() -> None:
    snapshot = SimpleNamespace(
        project_id="project",
        project_revision=1,
        variant_id="variant",
        variant_revision=2,
        sources=(),
        effective_version_id=None,
        config_digest="c" * 64,
    )
    commands = _Commands()
    presenter = TerminologyPresenter(
        TerminologyUiServices(build_inputs=_Inputs(snapshot), commands=commands),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )

    assert [label for _area, label, _icon in TERMINOLOGY_AREAS] == ["概览", "术语", "版本", "报告"]
    assert presenter.preflight().ready
    assert presenter.start_build().run_id == "build-run"
    assert presenter.publish().run_id == "publish-run"
    assert presenter.render_report().run_id == "report-run"
    assert presenter.render_changelog().run_id == "changelog-run"
    assert [call[0] for call in commands.calls] == ["build", "publish", "report", "changelog"]
