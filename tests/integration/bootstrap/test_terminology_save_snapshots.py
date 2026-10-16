from collections import Counter
from dataclasses import replace

import pytest

from tests.integration.bootstrap.test_terminology_build_review import _Harness
from transbridge.application.terminology.drafts import DraftWriteExpectation, revised_draft
from transbridge.application.terminology.identity import canonical_digest
from transbridge.application.terminology.models import DecisionStatus, TerminologyDraft, TerminologyVersion
from transbridge.persistence.terminology import SqliteTerminologyRepository, repository as repository_module

pytestmark = pytest.mark.integration


def test_single_term_save_reuses_snapshots_without_changing_audit_or_digest(tmp_path, monkeypatch) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        harness = _Harness(repository, monkeypatch)
        harness.build()
        harness.publish()
        before = harness.build()
        term = before.decisions[0]
        counts = Counter()
        original_loads = repository_module.loads

        def record_decode(payload, expected_type):
            counts[expected_type] += 1
            return original_loads(payload, expected_type)

        with monkeypatch.context() as patch:
            patch.setattr(repository_module, "loads", record_decode)
            saved = harness.commands.apply_decision(
                "change_translation", harness.context, term_id=term.term_id, translation="巨龙"
            )
        assert counts[TerminologyDraft] == 2
        assert counts[TerminologyVersion] == 0
        expected_decisions = (replace(term, translation="巨龙", status=DecisionStatus.MANUAL_CONFIRMED),)
        assert saved.decisions == expected_decisions
        action = saved.actions[-1]
        namespace = "terminology.manual-decision-state.v1"
        assert action.before_digest == canonical_digest(before.decisions, namespace=namespace)
        assert action.after_digest == canonical_digest(expected_decisions, namespace=namespace)
        expected = revised_draft(
            before,
            decisions=expected_decisions,
            actions=(*before.actions, action),
            digest_context={"manual_action": action.action_id},
        )
        assert saved == expected
        assert repository.active_draft("project-1", "main") == saved
        assert repository.list_manual_actions(saved.ref).items == saved.actions
    finally:
        repository.close()


def test_reference_expectation_rejects_cross_project_or_variant(tmp_path) -> None:
    from tests.persistence.terminology.test_draft_transactions import _empty_draft, _line

    line = _line()
    ref = _empty_draft("draft", line).ref
    for changed in (replace(line, project_id="another"), replace(line, variant_id="another")):
        with pytest.raises(ValueError, match="Project/Variant line"):
            DraftWriteExpectation.from_ref(ref, changed)
