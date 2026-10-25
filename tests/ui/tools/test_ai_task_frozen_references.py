"""Recovery restores the task's reference decisions before admission."""

from types import SimpleNamespace

from transbridge.ai_translator.project_reference_snapshot import capture_project_reference
from transbridge.ai_translator.project_terminology_runtime import freeze_project_terminology
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.ui.tools.ai_translator import run_request_preparation
from transbridge.ui.tools.ai_translator.run_request_preparation import prepare_run_request
from transbridge.ui.tools.ai_translator.task_references import (
    reference_store_for_request,
    restored_reference_binding,
)


def request(tmp_path, **updates):
    values = dict(
        run_id="original",
        recovery_directory=tmp_path,
        config=SimpleNamespace(target_lang="zh", max_concurrent=2, api_key="not-a-reference"),
        spec=SimpleNamespace(owner=SimpleNamespace(project_id="p", variant_id="v")),
    )
    return SimpleNamespace(**(values | updates))


def test_new_task_and_restart_do_not_inherit_prior_references(tmp_path):
    original = reference_store_for_request(request(tmp_path))
    original.load_or_capture("A", lambda: ([TermEntry("Sword", "剑", "json")], ()))
    continuing = reference_store_for_request(request(tmp_path, run_id="resume", recovery_task_id="original"))
    assert continuing.load_or_capture("A", lambda: ([], ())).records[0].translation == "剑"
    fresh = reference_store_for_request(request(tmp_path, run_id="new-task"))
    assert fresh.load_or_capture("A", lambda: ([], ())).records == ()
    assert "not-a-reference" not in original.path.read_text(encoding="utf-8")


def test_legacy_origin_does_not_share_references_between_new_logical_tasks(tmp_path):
    legacy = {"task_id": "legacy", "origin_task_id": "ancestor", "legacy": True}
    first = reference_store_for_request(request(tmp_path, run_id="new-1", recovery_record=legacy))
    second = reference_store_for_request(request(tmp_path, run_id="new-2", recovery_record=legacy))
    first.load_or_capture("A", lambda: ([TermEntry("Sword", "第一份", "json")], ()))
    second.load_or_capture("A", lambda: ([TermEntry("Sword", "第二份", "json")], ()))
    assert first.task_id == "new-1"
    assert second.task_id == "new-2"
    assert first.path != second.path
    continuing = reference_store_for_request(
        request(
            tmp_path,
            run_id="resumed-execution",
            recovery_task_id="new-1",
            recovery_record={"task_id": "new-1", "origin_task_id": "ancestor"},
        )
    )
    assert continuing.path == first.path
    assert continuing.load_or_capture("A", lambda: ([], ())).records[0].translation == "第一份"
    assert second.load_or_capture("A", lambda: ([], ())).records[0].translation == "第二份"


def test_background_preparation_uses_persisted_published_version_without_live_factory(tmp_path, monkeypatch):
    owner = SimpleNamespace(
        active_version_identity=("p", "v"),
        effective_terminology_factory=lambda *_: SimpleNamespace(
            effective_snapshot=lambda _: EffectiveTerminologySnapshot(
                "p", "v", EffectiveSnapshotStatus.READY, "old-version", "old-digest"
            )
        ),
    )
    original = reference_store_for_request(request(tmp_path))
    original.capture_published(capture_project_reference(freeze_project_terminology(owner)))
    ancestor = reference_store_for_request(request(tmp_path, run_id="ancestor"))
    ancestor.capture_published({"snapshot": None})
    restored = restored_reference_binding(
        {"task_id": "original", "origin_task_id": "ancestor"},
        tmp_path,
        project_id="p",
        variant_id="v",
        target_lang="zh",
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Recovery must not follow the currently published version")

    monkeypatch.setattr(run_request_preparation, "freeze_project_terminology", forbidden)
    prepared = prepare_run_request(
        "owner",
        "translate",
        request(tmp_path).config,
        (SimpleNamespace(id="one", key="one", original="Sword"),),
        project_id="p",
        variant_id="v",
        esp_path="A.esp",
        restored_terminology_binding=restored,
    )
    assert prepared.terminology_binding.snapshot_ref.version_id == "old-version"
    assert prepared.spec.terminology_snapshot == restored.snapshot_ref


def test_old_record_without_complete_snapshot_has_explicit_capture_fallback(tmp_path, caplog):
    with caplog.at_level("INFO"):
        assert (
            restored_reference_binding(
                {"task_id": "legacy"}, tmp_path, project_id="p", variant_id="v", target_lang="zh"
            )
            is None
        )
    assert "本次恢复将捕获一次" in caplog.text
