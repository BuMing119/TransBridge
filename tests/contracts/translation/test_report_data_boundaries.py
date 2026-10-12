"""Business report evidence and entry identities survive security projection."""

from dataclasses import replace
import json

import pytest

from transbridge.ai_translator.project_terminology_adapter import plugin_id_from_entry
from transbridge.ai_translator.translation_entry_outcomes import _owns
from transbridge.application.contracts import Diagnostic, OperationOutcome
from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace, StagePolicy
from transbridge.application.security import DiagnosticValue, SecretValue
from transbridge.application.translation.postprocess import (
    PostProcessCandidate,
    PostProcessStageOutcome,
    PostProcessWorkload,
    ReportSnapshot,
)
from transbridge.application.translation.postprocess_checkpoint import (
    InMemoryPostProcessCheckpointPort,
    PostProcessCheckpoint,
    checkpoint_entry_from_candidate,
    restore_candidates_from_checkpoint,
)
from transbridge.application.translation.postprocess_stages import _EntryView
from transbridge.application.translation.task_history import restore_report_snapshot
from transbridge.application.translation.workload_models import TranslationInput

TEXT = "The password: swordfish; token=dragonsoul."
KEY = EntryKey(SourceNamespace("source"), "quest", TEXT)
PLUGIN = "password=swordfish.esp"


def _candidate(**changes):
    return replace(
        PostProcessCandidate("run", KEY, EntryRevision(1), TEXT, TEXT, TEXT, 1, context=TEXT),
        **changes,
    )


def _snapshot(candidate, diagnostics=()):
    return ReportSnapshot("report", "run", OperationOutcome.COMPLETED, 1, 1, (candidate,), (), diagnostics)


def test_report_business_evidence_and_diagnostic_identity_survive_roundtrip():
    details = {
        "note": TEXT,
        "refined_translation": TEXT,
        "changes": [{"before": TEXT, "after": TEXT}],
        "issues": [{"original": TEXT, "translation": TEXT, "entry_key": KEY.to_dict()}],
        "token": "a game token",
        "password": "a quest password",
        "prompt": "an in-game prompt",
    }
    candidate = _candidate(report_details=tuple(details.items()))
    diagnostic = Diagnostic("QUEST_ISSUE", TEXT, details=(("entry_key", KEY.to_dict()), ("suggestion", TEXT)))
    snapshot = _snapshot(candidate, (diagnostic,))
    details["changes"][0]["before"] = "mutated"

    restored = restore_report_snapshot(json.loads(json.dumps(snapshot.to_dict())))
    actual = restored.candidates[0]
    evidence = dict(actual.report_details)
    assert (actual.original, actual.before_text, actual.text, actual.context) == (TEXT,) * 4
    assert actual.entry_key == KEY
    assert evidence["note"] == evidence["refined_translation"] == TEXT
    assert evidence["changes"] == [{"before": TEXT, "after": TEXT}]
    assert evidence["issues"][0]["entry_key"] == KEY.to_dict()
    assert evidence["password"] == "a quest password"
    assert evidence["token"] == "a game token"
    assert evidence["prompt"] == "an in-game prompt"
    assert restored.diagnostics[0].message == TEXT
    assert _owns(dict(restored.diagnostics[0].details), KEY)
    # Same comparison used by the task history details UI.
    assert dict(restored.diagnostics[0].details)["entry_key"] == actual.to_dict()["entry_key"]


def test_report_explicit_credentials_and_technical_diagnostics_remain_protected():
    candidate = _candidate(report_details=(("secret_field", SecretValue("opaque-credential")),))
    diagnostic = Diagnostic(
        "HTTP_FAILED",
        "Provider request failed.",
        details=(("entry_key", KEY.to_dict()), ("upstream", DiagnosticValue({"authorization": "short-secret"}))),
    )
    payload = _snapshot(candidate, (diagnostic,)).to_dict()
    serialized = json.dumps(payload)
    assert "opaque-credential" not in serialized
    assert "short-secret" not in serialized
    assert payload["entries"][0]["original"] == TEXT
    assert payload["diagnostics"][0]["details"]["entry_key"] == KEY.to_dict()


def test_new_and_legacy_report_scope_use_explicit_field():
    legacy = _candidate(report_details=(("terminology_plugin_id", PLUGIN), ("note", TEXT)))
    assert legacy.terminology_plugin_id == PLUGIN
    assert "terminology_plugin_id" not in dict(legacy.report_details)
    assert plugin_id_from_entry(_EntryView(legacy)) == PLUGIN
    payload = _snapshot(legacy).to_dict()
    assert payload["entries"][0]["terminology_plugin_id"] == PLUGIN
    assert restore_report_snapshot(payload).candidates[0].terminology_plugin_id == PLUGIN

    old_payload = _snapshot(_candidate()).to_dict()
    old_payload["entries"][0]["report_details"]["terminology_plugin_id"] = PLUGIN
    restored = restore_report_snapshot(old_payload).candidates[0]
    assert restored.terminology_plugin_id == PLUGIN
    assert plugin_id_from_entry(restored) == PLUGIN
    explicit = _candidate(terminology_plugin_id=PLUGIN, report_details=(("terminology_plugin_id", "stale.esp"),))
    assert explicit.terminology_plugin_id == PLUGIN
    assert _candidate(report_details=(("terminology_plugin_id", ""),)).terminology_plugin_id is None
    with pytest.raises(ValueError, match="terminology plugin ID"):
        _candidate(terminology_plugin_id="")


def test_checkpoint_roundtrip_retains_scope_and_old_checkpoint_still_loads():
    for plugin in (None, PLUGIN):
        candidate = _candidate(terminology_plugin_id=plugin)
        item = checkpoint_entry_from_candidate(candidate, phase="proofread", accepted=True)
        checkpoint = PostProcessCheckpoint("run", "owner", "a" * 64, entries=(item,))
        loaded = PostProcessCheckpoint.from_dict(json.loads(json.dumps(checkpoint.to_dict())))
        assert loaded.entries[0].candidate_hash() == loaded.entries[0].candidate_sha256
        restored = restore_candidates_from_checkpoint(loaded)[0]
        assert restored.terminology_plugin_id == plugin
        assert restored.entry_key == KEY and restored.text == TEXT
        if plugin is None:
            assert "terminology_plugin_id" not in checkpoint.to_dict()["entries"][0]


def test_postprocess_workload_retains_scope_across_resume():
    seen = []

    def stage(phase):
        def check(candidates):
            seen.extend(plugin_id_from_entry(_EntryView(item)) for item in candidates)
            return PostProcessStageOutcome(phase, candidates)

        return check

    port = InMemoryPostProcessCheckpointPort()
    workload = PostProcessWorkload(
        (stage("first"), stage("second")),
        stage_policy=StagePolicy(),
        stage_names=("first", "second"),
        checkpoint_port=port,
    )
    entry = TranslationInput(KEY, EntryRevision(1), TEXT, TEXT, 1, terminology_plugin_id=PLUGIN)
    result = workload.run("run", (entry,), owner_id="owner")
    assert result.value.candidates[0].terminology_plugin_id == PLUGIN
    assert seen == [PLUGIN, PLUGIN]
    # Simulate a pre-fix checkpoint that did not persist terminology scope.
    checkpoint = port.load("run")
    old_item = replace(checkpoint.entries[0], terminology_plugin_id=None)
    old_item = replace(old_item, candidate_sha256=old_item.candidate_hash())
    port.save(replace(checkpoint, revision=checkpoint.revision + 1, entries=(old_item,)))
    seen.clear()
    workload.run("run", (entry,), owner_id="owner", resume_after_phase="first")
    assert seen == [PLUGIN]
