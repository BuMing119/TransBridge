from __future__ import annotations

from dataclasses import replace
import time
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QDialog
import pytest

from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome
from transbridge.application.translation.task_scope import SourceTask
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.tools.ai_translator import task_session
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome
from transbridge.ui.tools.ai_translator.task_progress import AiTaskProgressWindow

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("preview_action", ["no_polish", "reject_candidates", "cancel_dialog"])
def test_unified_mixed_completion_distinguishes_rejecting_candidates_from_cancelling_task(
    monkeypatch, preview_action: str
) -> None:
    entry = TranslationEntry("one", "one", "Source", "", 0, "NPC_:FULL")
    polish_entry = TranslationEntry("two", "two", "Other source", "旧译文", 1, "NPC_:FULL")
    collection = TranslationEntryCollection([entry, polish_entry])
    slot = CollectionSlot("plugin", collection)
    published = []

    class Context:
        active_version_identity = ("project", "variant")
        slots = {"plugin": slot}
        collection_changed = SimpleNamespace(emit=published.append)
        uses_authoritative_projection = True

        @property
        def collection(self):
            return slot.collection

    context = Context()
    from tests.ui.tools.test_ai_task_session import _Persistence

    persistence = _Persistence(context, context.active_version_identity)
    monkeypatch.setattr(task_session, "VersionPersistence", lambda *_args: persistence)
    spec = SimpleNamespace(mode="mixed", run_id="run", execution_profile=SimpleNamespace(preview_enabled=True))
    source = SourceTask(
        "plugin", "plugin", None, collection, (entry,), () if preview_action == "no_polish" else (polish_entry,)
    )
    session = task_session.TaskSession(context, (source,), spec)
    errors, ready = [], []
    session.capture_before(on_success=ready.append, on_error=errors.append)
    deadline = time.monotonic() + 5
    while session.is_busy and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.001)
    assert not session.is_busy and ready and not errors
    detached = session.tasks[0]
    detached.collection.add(replace(detached.translate_entries[0], translation="Translated", stage=1), overwrite=True)
    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator._polish_preview_dialog._PolishPreviewDialog",
        lambda *_args, **_kwargs: SimpleNamespace(
            exec=lambda: (
                QDialog.DialogCode.Rejected if preview_action == "cancel_dialog" else QDialog.DialogCode.Accepted
            ),
            get_results=lambda: {"two": None},
            setWindowTitle=lambda _title: None,
        ),
    )
    from tests.ui.tools.ai_runtime_support import admit_ai_request

    request, activity = admit_ai_request(
        SimpleNamespace(spec=spec, run_id="run"), session.tasks, identity=session.version_identity
    )
    window = AiTaskProgressWindow(request, session, activity)
    monkeypatch.setattr(window.run, "_record", lambda: None)
    window.run.preparing = False
    window.run.worker = SimpleNamespace(was_cancelled=False, stop=lambda: None)
    window.show()
    outcome = SourceOutcome(
        detached,
        translation=SimpleNamespace(
            success_count=1,
            entry_outcomes={entry.identity: TranslationEntryOutcome(entry.identity, "succeeded", "Translated", 1)},
        ),
        polish={
            "two": SimpleNamespace(
                confidence=1, accepted=True, processing_status="completed", polished_translation="候选", note=""
            )
        },
    )
    window.run._completed((outcome,))
    window.run.worker = None
    deadline = time.monotonic() + 5
    while window.run.busy and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.001)
    assert not window.run.busy
    session.rollback_uncommitted()
    committed = preview_action != "cancel_dialog"
    assert session.completed is committed and session.saved is committed
    assert not session.can_save
    assert context.collection.get("one").translation == ("Translated" if committed else "")
    assert context.collection.get("two").translation == "旧译文"
    assert context.collection is collection
    assert collection.get("one").translation == ("Translated" if committed else "")
    assert collection.get("one").original == "Source"
    assert collection.get("two").translation == "旧译文"
    assert published == ([context.collection] if committed else [])
    assert len(persistence.commits) == int(committed)
    if preview_action == "reject_candidates":
        assert window.run.entries.entries[polish_entry.identity].decision == "rejected"
        assert polish_entry.identity not in window.run.entries.failed_keys
    window.run.worker = None
    window.close()
