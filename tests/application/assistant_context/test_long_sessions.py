from dataclasses import replace

from tests.application.assistant_context.test_compaction import Summarizer, epoch, item
from transbridge.application.assistant_context.compaction import compact
from transbridge.infra.assistant_prompt_cache import decorate_assistant_messages
from transbridge.persistence.assistant_context_store import AssistantContextStore
from transbridge.persistence.assistant_transcript_store import AssistantTranscriptStore
from transbridge.smart_assistant.context_budget import ContextBudget


def test_three_compressions_restore_all_summary_bytes_in_actual_dispatch_material(tmp_path):
    budget = ContextBudget(7000, 300, 50, estimator=len, estimator_label="test-characters")
    store = AssistantContextStore(AssistantTranscriptStore(str(tmp_path)))
    current, saved = epoch(), None
    generator = Summarizer()
    previous_blocks = []
    for cycle in range(3):
        incoming = tuple(item(f"{cycle}-{n}") for n in range(8)) + (item(f"u-{cycle}", "continue", "user"),)
        current = replace(
            current,
            items=current.items + incoming,
            source_digests=current.source_digests + tuple((i.item_id, i.source_digest) for i in incoming),
        )
        candidate = compact(current, budget, (), {}, generator)
        saved = store.stage(candidate, saved)
        reopened = AssistantContextStore(AssistantTranscriptStore(str(tmp_path)))
        current = reopened.read("s", "r", (), saved.head).epoch
        wire = decorate_assistant_messages(current.messages, namespace="same-request-config")
        blocks = [m["content"] for m in wire if '"kind":"request_history_summary"' in m.get("content", "")]
        assert blocks[: len(previous_blocks)] == previous_blocks
        assert 1 <= len(blocks) - len(previous_blocks) <= 3
        assert budget.measure(wire).fits
        previous_blocks = blocks
