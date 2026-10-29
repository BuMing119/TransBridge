from types import SimpleNamespace

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.preflight.inputs import ORIGINAL_KNOWN
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.tools.input_prerequisites import source_input_failure, source_slot_for
from transbridge.smart_assistant.tools.postprocess_task_run import launch_postprocess_task
from transbridge.smart_assistant.tools.tool_editor import EditorController
from transbridge.smart_assistant.tools.tool_translator import TranslationController


@pytest.fixture
def saved_context():
    namespace = SourceNamespace("saved")
    entries = (
        TranslationEntry(
            "known",
            "known",
            "Actual saved original",
            "Saved translation",
            1,
            None,
            entry_key=EntryKey(namespace, "known", "Actual saved original"),
        ),
        TranslationEntry(
            "unknown",
            "unknown",
            "",
            "Saved translation",
            1,
            None,
            entry_key=EntryKey(namespace, "unknown"),
            metadata=((ORIGINAL_KNOWN, False),),
        ),
    )
    slot = SimpleNamespace(
        collection=TranslationEntryCollection(entries),
        source_snapshot=None,
        source_id="source-saved",
        source_namespace="saved",
    )
    return SimpleNamespace(
        slots={"source-saved": slot},
        collection=slot.collection,
        active_slot=slot,
        active_key="source-saved",
        active_version_identity=("project", "variant"),
        runtime_context=RequestContext("operator"),
        filter_state={},
        entry_labels={},
    )


def test_known_saved_original_needs_no_artifact_but_template_still_does(saved_context):
    known = next(iter(saved_context.collection))
    assert source_input_failure(saved_context, (known,)) is None
    result = source_input_failure(saved_context, requirement="template", source_keys=("source-saved",))
    assert not result.success and result.data["source_input_repair"]["requirement"] == "template"
    assert source_slot_for(saved_context, known) is saved_context.active_slot


def test_template_repair_keeps_the_complete_original_batch_scope(saved_context):
    good = TranslationEntry(
        "good",
        "good",
        "Good original",
        "Good translation",
        1,
        None,
        entry_key=EntryKey(SourceNamespace("good"), "good"),
    )
    saved_context.slots["source-good"] = SimpleNamespace(
        collection=TranslationEntryCollection((good,)),
        source_snapshot=object(),
        source_id="source-good",
        source_namespace="good",
    )
    result = source_input_failure(saved_context, requirement="template", source_keys=("source-good", "source-saved"))
    repair = result.data["source_input_repair"]
    assert repair["source_keys"] == ["source-good", "source-saved"]
    assert repair["missing_source_keys"] == ["source-saved"]


@pytest.mark.parametrize("mode", ["translate", "polish", "mixed"])
def test_assistant_unknown_original_returns_repair_before_task_or_model_setup(saved_context, monkeypatch, mode):
    monkeypatch.setattr(
        "transbridge.paratranz.config_manager.LLMConfig.load_from_file",
        lambda: pytest.fail("Unknown original must not start model preparation"),
    )
    result = TranslationController().start_translation(
        {"mode": mode, "entry_ids": ["unknown"]}, saved_context, saved_context.collection
    )
    assert not result.success and "task_id" not in result.data
    repair = result.data["source_input_repair"]
    assert repair["source_keys"] == ["source-saved"]
    assert repair["entry_keys"] == [saved_context.collection.get("unknown").identity.to_dict()]
    assert (repair["project_id"], repair["variant_id"]) == ("project", "variant")


def test_postprocess_checks_the_resolved_scope_once_and_preserves_repair_receipt(saved_context, monkeypatch):
    resolved = []

    def resolve(_args, _ctx, collection):
        resolved.append(collection)
        return SimpleNamespace(entries=(collection.get("unknown"),), stages=("proofread",))

    monkeypatch.setattr("transbridge.smart_assistant.tools.postprocess_task_run.resolve_postprocess_request", resolve)
    monkeypatch.setattr(
        "transbridge.smart_assistant.tools.postprocess_task_run.AssistantTaskResults.capture",
        lambda *_args, **_kwargs: pytest.fail("Missing original must not create a task"),
    )
    result = launch_postprocess_task(
        {}, saved_context, report_directory=lambda _: None, report_callback=lambda *_: None
    )
    assert len(resolved) == 1
    assert result.error_code == "SOURCE_INPUT_REQUIRED"
    assert result.data["source_input_repair"]["entry_keys"][0]["local_key"] == "unknown"


def test_reading_saved_entries_distinguishes_missing_original_from_real_text(saved_context):
    entries = EditorController().get_visible_entries({}, saved_context, saved_context.collection).data["entries"]
    assert entries[0]["original_known"] is True and entries[0]["original"] == "Actual saved original"
    assert entries[1]["original_known"] is False and entries[1]["original"] == ""
