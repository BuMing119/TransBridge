from __future__ import annotations

import json

import pytest

from transbridge.application.translation.postprocess import PostProcessCandidate
from transbridge.application.translation.postprocess_progress import PostProcessProgress
from transbridge.application.translation.postprocess_stages import (
    CheckerStage,
    LlmPostProcessStage,
    PostProcessLlmError,
    PostProcessLlmPhase,
    PostProcessLlmResponse,
)
from transbridge.application.translation.proofread_stage import ProofreadStage
from transbridge.converter.translation_entry import TranslationEntry


def _candidates():
    entries = [TranslationEntry(str(i), str(i), "Hello", "你好", 1, "") for i in range(2)]
    return tuple(
        PostProcessCandidate("run", e.identity, e.revision, e.original, e.translation, e.translation, e.stage)
        for e in entries
    )


def test_callable_proofread_exposes_start_and_live_batch_progress():
    progress, logs = [], []
    reporter = PostProcessProgress(lambda *args: progress.append(args), logs.append)

    class Client:
        calls = 0

        def chat_prepared(self, prepare, max_tokens=0):
            # The next model request sees the preceding batch's progress immediately.
            assert progress[-1][:3] == ("proofread", self.calls, 2)
            assert logs[0] == "开始校对，共 2 条"
            if self.calls:
                assert "校对进度 1/2 条" in logs
            self.calls += 1
            payload = json.loads(prepare()[1]["content"])
            return json.dumps({
                "results": [
                    {"entry_key": item["entry_key"], "final_translation": "您好"} for item in payload["entries"]
                ]
            })

    outcome = ProofreadStage(
        Client(),
        max_items=1,
        progress_callback=reporter.for_phase("proofread"),
        event_callback=reporter.event,
    )(_candidates())
    assert progress[-1][:3] == ("proofread", 2, 2)
    assert all(candidate.accepted and candidate.text == "您好" for candidate in outcome.candidates)
    assert "开始检查术语" in logs


@pytest.mark.parametrize("phase", ["consistency", "format", "quality_gate"])
def test_checker_reports_progress_even_for_failed_items(phase):
    progress = []
    reporter = PostProcessProgress(lambda *args: progress.append(args), None)

    class Checker:
        calls = 0

        def check(self, entry):
            assert progress[-1][:3] == (phase, self.calls, 2)
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("checker failed")
            return []

    outcome = CheckerStage(phase, Checker(), progress_callback=reporter.for_phase(phase))(_candidates())
    assert progress[-1][:3] == (phase, 2, 2)
    assert len(outcome.candidates) == 2
    assert [d.code for d in outcome.diagnostics] == ["POSTPROCESS_CHECK_FAILED"]


@pytest.mark.parametrize("phase", list(PostProcessLlmPhase))
@pytest.mark.parametrize("batched", [True, False])
def test_strict_llm_stages_report_start_and_attempted_items(phase, batched):
    progress = []
    reporter = PostProcessProgress(lambda *args: progress.append(args), None)

    class Port:
        completed = 0

        def apply(self, actual_phase, request):
            assert actual_phase == phase
            assert progress[-1][:3] == (phase.value, self.completed, 2)
            self.completed += len(request.candidates)
            if self.completed == 1:
                raise PostProcessLlmError("MODEL_UNAVAILABLE", "Model unavailable")
            value = "pass" if phase is PostProcessLlmPhase.ARBITRATE else "您好"
            return PostProcessLlmResponse(tuple((c.entry_key, value) for c in request.candidates), "a" * 64)

    outcome = LlmPostProcessStage(
        phase,
        Port(),
        max_tokens_per_batch=1000 if batched else None,
        max_items=1,
        progress_callback=reporter.for_phase(phase.value),
    )(_candidates())
    assert progress[-1][:3] == (phase.value, 2, 2)
    assert bool(outcome.diagnostics) is batched


def test_disconnected_progress_observer_does_not_fail_processing(caplog):
    def broken(*args):
        raise RuntimeError("window disconnected")

    reporter = PostProcessProgress(broken, broken)
    reporter.for_phase("polish")(0, 2, "")
    reporter.for_phase("polish")(1, 2, "")
    assert "progress callback failed" in caplog.text
    assert "event callback failed" in caplog.text
