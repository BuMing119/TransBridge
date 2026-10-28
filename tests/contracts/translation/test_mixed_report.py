from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.ui.tools.ai_runtime_support import ai_task_runtime
from transbridge.application.contracts import OperationOutcome
from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.translation.mixed_report import build_mixed_report_snapshot
from transbridge.application.translation.postprocess import PostProcessCandidate, ReportSnapshot
from transbridge.application.translation.postprocess_report import render_report_bundle


def _snapshot(schema: str, run_id: str, local_key: str) -> ReportSnapshot:
    candidate = PostProcessCandidate(
        run_id=run_id,
        entry_key=EntryKey(SourceNamespace.legacy(), local_key),
        before_revision=EntryRevision(),
        original=f"original:{local_key}",
        before_text=f"before:{local_key}",
        text=f"after:{local_key}",
        stage=2,
        phases=("polish",) if schema.endswith("polish-report.v1") else (),
    )
    return ReportSnapshot(
        schema,
        run_id,
        OperationOutcome.COMPLETED,
        1,
        1,
        (candidate,),
        (),
        (),
        run_spec_summary={"branch": local_key},
    )


def test_mixed_report_combines_both_branches_under_parent_run_identity() -> None:
    snapshot = build_mixed_report_snapshot(
        _snapshot("transbridge.postprocess-report.v1", "mixed-run", "translate"),
        _snapshot("transbridge.polish-report.v1", "mixed-run", "polish"),
        run_id="mixed-run",
        execution_order="parallel",
        run_spec_summary={"config_digest": "digest"},
    )

    assert snapshot.schema == "transbridge.mixed-report.v1"
    assert snapshot.run_id == "mixed-run"
    assert {candidate.entry_key.local_key for candidate in snapshot.candidates} == {"translate", "polish"}
    assert all(candidate.run_id == snapshot.run_id for candidate in snapshot.candidates)
    assert snapshot.input_count == snapshot.accepted_count == 2
    assert snapshot.run_spec_summary["execution_order"] == "parallel"
    assert snapshot.run_spec_summary["translation"] == {"branch": "translate"}
    assert snapshot.run_spec_summary["polish"] == {"branch": "polish"}


def test_mixed_report_rejects_a_branch_from_another_run() -> None:
    with pytest.raises(ValueError, match="parent run ID"):
        build_mixed_report_snapshot(
            _snapshot("transbridge.postprocess-report.v1", "translation-run", "translate"),
            None,
            run_id="mixed-run",
            execution_order="serial",
        )


def test_mixed_report_bundle_uses_a_distinct_artifact_stem(tmp_path: Path) -> None:
    snapshot = build_mixed_report_snapshot(
        _snapshot("transbridge.postprocess-report.v1", "mixed-run", "translate"),
        None,
        run_id="mixed-run",
        execution_order="serial",
    )

    rendered = render_report_bundle(snapshot, base_dir=tmp_path)

    assert rendered.value is not None
    assert rendered.value.run_id == "mixed-run"
    assert {Path(item.artifact_path).name.split("-")[0] for item in rendered.value.artifacts} == {"mixed"}
    assert all(Path(item.artifact_path).name.startswith("mixed-report-") for item in rendered.value.artifacts)


def test_mixed_result_actions_register_the_real_excel_artifact(tmp_path: Path) -> None:
    from transbridge.ui.tools.ai_translator.reporting import TranslationReportArtifacts
    from transbridge.ui.tools.ai_translator.result_actions import AiResultNavigator, result_action_state
    from transbridge.ui.tools.ai_translator.run_controller import RunController

    report_path = tmp_path / "mixed-report-fixture.xlsx"
    report_path.write_bytes(b"xlsx")
    controller = RunController(task_runtime=ai_task_runtime(), owner_id="mixed-owner")
    request = controller.begin(
        "mixed",
        SimpleNamespace(api_key="secret", provider="fixture", model="fixture", max_concurrent=3),
        [SimpleNamespace(id="entry", key="entry", original="source", translation="draft", stage=2)],
    )

    class Progress:
        def set_result_actions(self, state) -> None:
            self.result_actions = state

    progress = Progress()
    result = {
        "translate": SimpleNamespace(failed_entries=()),
        "polish": None,
        "artifacts": TranslationReportArtifacts((str(report_path),), str(report_path), ()),
    }

    progress.result_navigator = AiResultNavigator()
    artifact = progress.result_navigator.register_report(request.spec, result["artifacts"].excel_path)
    progress.set_result_actions(result_action_state(request.spec, result=result["translate"], report=artifact))

    assert progress.result_actions.report is not None
    assert progress.result_navigator.report_path(progress.result_actions.report, request.spec.owner) == str(report_path)
