"""Cross-boundary regressions identified by the workflow closure audit."""

from types import SimpleNamespace
import zipfile

import pytest

from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.fomod import FomodTaskDraft, FomodTaskEntrypoint, FomodTaskPreflightService
from transbridge.application.io import EntryKey, Provenance, SourceNamespace
from transbridge.application.io.mutation import ChangeSet, EntryPatch, MutationStatus
from transbridge.application.tasks import CallbackThreadBackend, JobState, OwnerRef, TaskRuntime
from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.bootstrap.adapters import SystemClock, UuidGenerator
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.fomod.pipeline import FomodPipeline, PipelineResult, _select_fomod_ai_entry_ids
from transbridge.parser.plugin_parser import PluginParser
from transbridge.translation_memory import TranslationMemoryManager


@pytest.mark.parametrize("same_key", [False, True], ids=["ordinary-keys", "original-qualified-keys"])
def test_fomod_ai_translations_survive_plugin_writeback(tmp_path, monkeypatch, same_key):
    """An offline AI double accepts the same response identities as AutoTranslator."""
    source_dir = tmp_path / "plugins"
    source_dir.mkdir()
    source = write_plugin(
        source_dir / "source.esp",
        [(0x800, "First", "First"), (0x800 if same_key else 0x801, "First" if same_key else "Second", "Second")],
    )

    class OfflineTranslator:
        def __init__(self, config):
            pass

        def translate(self, collection, target_entry_ids, **kwargs):
            selected = [entry for entry in collection if ai_entry_key(entry) in target_entry_ids]
            if selected:
                result = collection.apply(
                    ChangeSet(
                        "offline-ai",
                        tuple(
                            EntryPatch.create(entry.identity, translation=f"Translated {entry.original}", stage=1)
                            for entry in selected
                        ),
                        tuple((entry.identity, entry.revision) for entry in selected),
                        Provenance("offline-ai", "test", "translation"),
                    ),
                    RequestContext(
                        "test",
                        run_id="offline-ai",
                        permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
                    ),
                )
                assert result.status is MutationStatus.APPLIED
            return SimpleNamespace(success_count=len(selected), failed_count=0)

    monkeypatch.setattr("transbridge.ai_translator.translator.AutoTranslator", OfflineTranslator)
    pipeline = FomodPipeline(
        llm_config=SimpleNamespace(target_lang="zh_CN"),
        tm_manager=TranslationMemoryManager(tmp_path / "empty-dictionaries"),
    )
    result = PipelineResult()
    pipeline._translate_plugins(source_dir, None, result, None, None, target_locale="zh_CN", run_id="audit-fomod")

    assert [entry.original for entry in PluginParser().parse_plugin(source)] == [
        "Translated First",
        "Translated Second",
    ]
    assert result.ai_translated == 2
    assert result.unresolved == 0


def test_fomod_selection_preserves_exact_original_namespace_and_stage_policy():
    entries = [
        TranslationEntry(
            "same",
            "same",
            original,
            "",
            stage,
            "NPC_:FULL",
            entry_key=EntryKey(SourceNamespace(namespace), "same", original),
        )
        for namespace, original, stage in [
            ("plugin:first", "First", 0),
            ("plugin:first", " First ", 0),
            ("plugin:second", "First", 0),
            ("plugin:first", "Locked", 9),
            ("plugin:first", "Hidden", -1),
        ]
    ]
    collection = TranslationEntryCollection(entries)
    assert _select_fomod_ai_entry_ids(collection) == [ai_entry_key(entry) for entry in entries[:3]]
    selected = frozenset(entry.identity for entry in entries[1:])
    assert _select_fomod_ai_entry_ids(collection, entry_keys=selected) == [
        ai_entry_key(entry) for entry in entries[1:3]
    ]
    assert _select_fomod_ai_entry_ids(collection, entry_keys=frozenset()) == []


def test_fomod_engine_setup_failure_leaves_no_active_task(tmp_path):
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("fomod/info.xml", "<fomod />")
    checked = FomodTaskPreflightService().preflight(
        FomodTaskDraft(
            str(source),
            str(tmp_path / "output.zip"),
            "zh_CN",
            "audit-config",
            workspace_root=str(tmp_path / "work"),
        )
    )
    assert checked.ready
    runtime = TaskRuntime(
        id_generator=UuidGenerator(),
        clock=SystemClock(),
        backend=CallbackThreadBackend(lambda _run_id, target: target()),
    )
    owner = OwnerRef("gui", "fomod", session_id="audit-session")

    def broken_engine_factory(spec, run_guard, commit_guard):
        raise OSError("LLM configuration could not be read")

    try:
        entrypoint = FomodTaskEntrypoint(runtime, broken_engine_factory)
        ref = entrypoint.submit(checked, owner)
        assert runtime.get(ref, owner).state is JobState.FAILED
        assert entrypoint.report(ref, owner) is None
        assert all(task.is_terminal for task in runtime.list(owner)), "setup failure left an orphaned queued task"
    finally:
        runtime.close()
