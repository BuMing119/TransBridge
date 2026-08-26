from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

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


@pytest.mark.parametrize("execution_order", ["serial", "parallel"])
def test_mixed_worker_finishes_with_snapshot_and_rendered_artifacts(monkeypatch, execution_order: str) -> None:
    from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadResult
    from transbridge.ui.tools.ai_translator import reporting
    from transbridge.ui.tools.ai_translator._mixed_worker import MixedPolishResult, _MixedWorker

    translate_entry = SimpleNamespace(
        id="translate",
        key="translate",
        identity=EntryKey(SourceNamespace.legacy(), "translate"),
        revision=EntryRevision(),
        original="source",
        translation="translated",
        stage=2,
        context="",
    )
    polish_entry = SimpleNamespace(
        id="polish",
        key="polish",
        identity=EntryKey(SourceNamespace.legacy(), "polish"),
        revision=EntryRevision(),
        original="source",
        translation="draft",
        stage=2,
        context="",
    )
    translation_snapshot = _snapshot("transbridge.postprocess-report.v1", "mixed-run", "translate")
    translate_result = SimpleNamespace(
        success_count=1,
        failed_count=0,
        skipped_count=0,
        new_dynamic_terms=0,
        post_process_result=translation_snapshot,
    )
    proofread = ProofreadResult(
        entry_id="polish",
        entry_key="polish",
        original_translation="draft",
        polished_translation="polished",
        confidence=0.9,
        needs_arbitration=False,
        note="",
        verdict="pass",
    )
    polish_result = MixedPolishResult(1, 0, (), {"polish": proofread})
    rendered = reporting.TranslationReportArtifacts(("report.xlsx",), "report.xlsx", ())
    monkeypatch.setattr(reporting, "render_snapshot_report", lambda snapshot, esp_stem: rendered)

    worker = _MixedWorker(
        SimpleNamespace(pp_polish_level="moderate"),
        [translate_entry],
        [polish_entry],
        execution_order=execution_order,
        ctx=SimpleNamespace(esp_path="fixture.esp"),
        run_id="mixed-run",
    )
    monkeypatch.setattr(worker, "_do_translate", lambda: translate_result)
    monkeypatch.setattr(worker, "_do_polish", lambda: polish_result)
    emitted: list[dict] = []
    worker.finished.connect(emitted.append)

    worker.run()

    assert len(emitted) == 1
    result = emitted[0]
    assert result["snapshot"].schema == "transbridge.mixed-report.v1"
    assert result["snapshot"].run_id == "mixed-run"
    assert result["artifacts"].excel_path == "report.xlsx"


def test_mixed_preview_rejection_is_authoritative_before_render(monkeypatch) -> None:
    from PyQt6.QtWidgets import QDialog

    from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadResult
    from transbridge.ui.tools.ai_translator import _polish_preview_dialog, _report_render_worker
    from transbridge.ui.tools.ai_translator._mixed_worker import MixedPolishResult
    from transbridge.ui.tools.ai_translator.result_view import apply_window_mixed_result

    entry = SimpleNamespace(
        id="polish",
        key="polish",
        identity=EntryKey(SourceNamespace.legacy(), "polish"),
        revision=EntryRevision(),
        original="source",
        translation="draft",
        stage=2,
        context="",
    )
    proofread = ProofreadResult(
        entry_id="polish",
        entry_key="polish",
        original_translation="draft",
        polished_translation="candidate",
        confidence=0.9,
        needs_arbitration=False,
        note="",
        verdict="pass",
    )

    class RejectDialog:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def exec(self):
            return QDialog.DialogCode.Rejected

    rendered = []
    monkeypatch.setattr(_polish_preview_dialog, "_PolishPreviewDialog", RejectDialog)
    monkeypatch.setattr(
        _report_render_worker,
        "start_report_render",
        lambda snapshot, esp_stem, **kwargs: rendered.append((snapshot, esp_stem)),
    )
    result = {"translate": None, "polish": MixedPolishResult(1, 0, (), {"polish": proofread})}
    window = SimpleNamespace(
        _active_mixed_polish_entries=[entry],
        _active_mixed_preview=True,
        _active_mixed_spec=SimpleNamespace(run_id="mixed-preview", input_fingerprint="input", config_digest="cfg"),
        _active_mixed_config=SimpleNamespace(pp_polish_level="moderate", mixed_execution_order="serial"),
        _active_mixed_progress=None,
        _theme_view=None,
        _ctx=SimpleNamespace(collection=object(), esp_path="fixture.esp"),
    )

    changed = apply_window_mixed_result(window, result)

    assert changed is False
    assert result["snapshot"].run_id == "mixed-preview"
    assert result["snapshot"].accepted_count == 0
    assert dict(result["snapshot"].candidates[0].report_details)["result_status"] == "rejected"
    assert rendered == [(result["snapshot"], "fixture")]


def test_mixed_result_actions_register_the_real_excel_artifact(tmp_path: Path) -> None:
    from transbridge.ui.tools.ai_translator.reporting import TranslationReportArtifacts
    from transbridge.ui.tools.ai_translator.run_controller import RunController, register_mixed_result_actions

    report_path = tmp_path / "mixed-report-fixture.xlsx"
    report_path.write_bytes(b"xlsx")
    controller = RunController(owner_id="mixed-owner")
    request = controller.begin(
        "mixed",
        SimpleNamespace(api_key="secret", provider="fixture", model="fixture"),
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

    register_mixed_result_actions(progress, request.spec, result)

    assert progress.result_actions.report is not None
    assert progress.result_navigator.report_path(progress.result_actions.report, request.spec.owner) == str(report_path)
