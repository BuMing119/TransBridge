"""Canonical report contracts: golden parity, safe render failure and secret-free output."""

from __future__ import annotations

import json

from transbridge.application.contracts import OperationOutcome
from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace, StagePolicy
from transbridge.application.translation import (
    CsvReportRenderer,
    ExcelReportRenderer,
    InMemoryPostProcessCheckpointPort,
    JsonReportRenderer,
    PostProcessStageOutcome,
    PostProcessWorkload,
    ReportSnapshot,
    TranslationInput,
    render_report,
)


def _refine(candidates):
    return PostProcessStageOutcome(
        "refine", tuple(candidate.with_text("refined-text", "refine") for candidate in candidates)
    )


def _run_snapshot(*, run_id: str = "golden-run") -> ReportSnapshot:
    workload = PostProcessWorkload(
        (_refine,),
        stage_policy=StagePolicy(),
        stage_names=("refine",),
        checkpoint_port=InMemoryPostProcessCheckpointPort(),
    )
    entry = TranslationInput(
        EntryKey(SourceNamespace("fixture"), "golden"), EntryRevision(3), "source-text", "draft-text", 2
    )
    result = workload.run(
        run_id,
        (entry,),
        owner_id="owner",
        run_spec_summary={"target_locale": "zh-CN", "config_revision": 7},
    )
    assert result.outcome is OperationOutcome.COMPLETED
    assert result.value is not None
    return result.value


def test_golden_counts_are_consistent_across_all_renderers(tmp_path) -> None:
    snapshot = _run_snapshot()
    result = render_report(
        snapshot,
        (JsonReportRenderer(), CsvReportRenderer(), ExcelReportRenderer()),
        base_dir=tmp_path,
    )

    assert result.outcome is OperationOutcome.COMPLETED
    assert result.value is not None
    assert result.value.produced == 3
    assert result.value.failed == 0

    parsed = json.loads(result.value.artifacts[0].content)
    assert parsed["schema"] == "transbridge.postprocess-report.v1"
    assert parsed["run_id"] == "golden-run"
    assert parsed["counts"]["input"] == 1
    assert parsed["counts"]["accepted"] == 1
    assert parsed["issues"] == 0
    assert parsed["failures"] == 0
    assert parsed["timing_ms"][0][0] == "refine"
    assert parsed["run_spec_summary"]["target_locale"] == "zh-CN"

    csv_text = result.value.artifacts[1].content.decode("utf-8")
    assert 'golden-run' in csv_text and 'refined-text' in csv_text
    assert csv_text.count("\n") == 2  # header + one entry

    excel_bytes = result.value.artifacts[2].content
    assert excel_bytes[:2] == b"PK"  # xlsx is a zip container
    artifacts = {artifact.renderer: artifact for artifact in result.value.artifacts}
    assert all(artifact.sha256 for artifact in artifacts.values())
    assert all(artifact.artifact_path for artifact in artifacts.values())


def test_renderer_failure_never_rolls_back_and_carries_report_diagnostic(tmp_path) -> None:
    snapshot = _run_snapshot(run_id="render-fail-run")

    class BrokenRenderer:
        name = "broken"

        def render(self, snapshot, *, base_dir=None):
            raise OSError("disk full")

    result = render_report(snapshot, (JsonReportRenderer(), BrokenRenderer()), base_dir=tmp_path)

    assert result.outcome is OperationOutcome.PARTIAL
    assert result.value is not None
    assert result.value.produced == 1
    assert result.value.failed == 1
    assert any(d.code == "REPORT_RENDER_FAILED" for d in result.diagnostics)
    assert result.counts.failed == 1


def test_report_never_contains_prompts_or_secrets() -> None:
    snapshot = _run_snapshot(run_id="secret-scan")
    rendered = json.dumps(snapshot.to_dict(), ensure_ascii=False)
    for forbidden in ("Prompt", "bearer", "sk-", "api_key", "Authorization"):
        assert forbidden not in rendered
    json_artifact = JsonReportRenderer().render(snapshot).content.decode("utf-8")
    assert "Authorization" not in json_artifact


def test_report_snapshot_fingerprint_is_stable_per_run_id() -> None:
    first = _run_snapshot(run_id="fp-run")
    second = _run_snapshot(run_id="fp-run")
    assert first.fingerprint == second.fingerprint
    third = _run_snapshot(run_id="fp-run-other")
    assert first.fingerprint != third.fingerprint