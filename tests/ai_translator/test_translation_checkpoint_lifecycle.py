"""Current candidates retain partial work without touching obsolete progress files."""

from threading import Event
from types import SimpleNamespace

import pytest

from tests.ai_translator import test_prepared_names as support
from transbridge.ai_translator.batch_planner import Batch, BatchPlan
from transbridge.application.translation import FilesystemTranslationCheckpointPort
from transbridge.application.translation.task_candidate_recovery import source_digest
from transbridge.application.translation.task_scope import SourceTask
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator.task_translation_resume import prepare_translation_recovery

make_translator = support.make_translator


class _SerialBatches:
    def run(self, chains, operation, *, stop_event, **_kwargs):
        for chain in chains:
            for batch in chain:
                if stop_event.is_set():
                    return
                operation(batch)


@pytest.mark.parametrize("existing", [False, True], ids=["no-legacy-files", "legacy-files-retained"])
@pytest.mark.parametrize("outcome", ["success", "cancel", "failure"])
def test_only_current_candidates_are_written_and_partial_work_resumes(
    make_translator, tmp_path, monkeypatch, existing, outcome
):
    root = tmp_path / "data" / "plugin"
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(root)))
    old_files = (root / "plugin_progress.json", root / "plugin_post_process.json")
    old_bytes = b'{"old-user-evidence": true, "run_id": "unrelated-run"}'
    if existing:
        root.mkdir(parents=True)
        for path in old_files:
            path.write_bytes(old_bytes)

    entries = (support._entry("first", original="First"), support._entry("second", original="Second"))
    collection = TranslationEntryCollection(entries)
    first, provider, _ = make_translator()
    run_id = f"task-owned-{source_digest('source')}"
    first._run_id_factory = lambda: run_id
    first._candidate_checkpoint_port = None  # Exercise the production runtime-v2 path.
    first._planner = SimpleNamespace(plan=lambda rows: BatchPlan(round3=[Batch([row], "长文本") for row in rows]))
    first._batch_executor = _SerialBatches()
    original_chat = provider.chat
    stop = Event()

    def chat(*args, **kwargs):
        if outcome == "failure" and provider.translation_calls:
            raise RuntimeError("second batch unavailable")
        return original_chat(*args, **kwargs)

    monkeypatch.setattr(provider, "chat", chat)
    durable_counts = []

    def progress(_current, _total, _message, succeeded, *_rest):
        for path in old_files:
            assert path.read_bytes() == old_bytes if existing else not path.exists()
        checkpoint = FilesystemTranslationCheckpointPort(root / "runtime-v2").load(run_id)
        if checkpoint is not None:
            durable_counts.append(len(checkpoint.candidates))
        if outcome == "cancel" and succeeded:
            stop.set()

    result = first.translate(collection, None, progress, stop)
    checkpoint = FilesystemTranslationCheckpointPort(root / "runtime-v2").load(run_id)
    assert checkpoint is not None
    assert len(checkpoint.candidates) == (2 if outcome == "success" else 1)
    assert durable_counts and durable_counts[0] == 1
    assert result.failed_count == int(outcome == "failure")
    for path in old_files:
        assert path.read_bytes() == old_bytes if existing else not path.exists()
    if outcome == "success":
        assert result.success_count == 2
        return

    original_checkpoint = checkpoint
    resumed_collection = TranslationEntryCollection(entries)
    source = SourceTask("source", "Source", "plugin.esp", resumed_collection, entries, ())
    record = {
        "task_id": "task-owned",
        "sources": [
            {
                "key": source.key,
                "checkpoint_stem": "plugin",
                "translate_keys": [entry.identity.to_dict() for entry in entries],
                "inputs": [
                    {
                        "entry_key": entry.identity.to_dict(),
                        "revision": entry.revision.value,
                        "original": entry.original,
                        "translation": entry.translation,
                        "stage": entry.stage,
                        "context": entry.context,
                    }
                    for entry in entries
                ],
            }
        ],
    }
    pause = Event()
    pause.set()
    recovered = prepare_translation_recovery(
        SimpleNamespace(
            request=SimpleNamespace(recovery_record=record),
            config=first._cfg.llm_config,
            stop=Event(),
            pause=pause,
            progress=lambda *_: None,
            log=lambda *_: None,
        ),
        (source,),
    )
    resumed, resumed_provider, _ = make_translator(prepared=recovered[source.key])
    resumed._candidate_checkpoint_port = None
    resumed._run_id_factory = lambda: f"{run_id}-attempt-1"
    completed = resumed.translate(resumed_collection, None, lambda *_: None, Event())
    assert resumed_provider.translation_calls == [("second",)]
    assert completed.success_count == 2 and completed.failed_count == 0
    assert all(item.status == "succeeded" for item in completed.entry_outcomes.values())
    assert FilesystemTranslationCheckpointPort(root / "runtime-v2").load(run_id) == original_checkpoint
    assert len(FilesystemTranslationCheckpointPort(root / "runtime-v2").load(f"{run_id}-attempt-1").candidates) == 2


def test_separate_run_does_not_reuse_another_runs_candidates(make_translator, tmp_path):
    port = FilesystemTranslationCheckpointPort(tmp_path / "runtime-v2")
    for run_id in ("first-run", "independent-run"):
        translator, provider, _ = make_translator(checkpoint=port)
        translator._run_id_factory = lambda run_id=run_id: run_id
        result, _ = support._translate(translator, [support._entry()])
        assert provider.translation_calls == [("name",)]
        assert result.success_count == 1
        assert len(port.load(run_id).candidates) == 1
