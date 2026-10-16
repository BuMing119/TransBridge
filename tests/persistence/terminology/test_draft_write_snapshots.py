from dataclasses import replace

import pytest

from tests.persistence.terminology.test_draft_transactions import _empty_draft, _line, _LineReader
from transbridge.application.terminology.drafts import DraftWriteExpectation, revised_draft
from transbridge.application.terminology.errors import RepositoryConflictError, RevisionConflictError
from transbridge.persistence.terminology import SqliteTerminologyRepository
from transbridge.persistence.terminology.codec import dumps


def test_active_ref_reads_complete_identity_without_loading_payload(tmp_path, monkeypatch) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        assert repository.active_ref("project-1", "variant-1") is None
        draft = _empty_draft("draft-1", _line())
        repository.create_draft(draft)
        statements = []
        repository._connection.set_trace_callback(statements.append)

        def forbid_decode(*args):
            pytest.fail("reference reads must not deserialize the full draft")

        with monkeypatch.context() as patch:
            patch.setattr(repository._drafts, "_decode", forbid_decode)
            assert repository.active_ref("project-1", "variant-1") == draft.ref
            assert repository.active_ref("project-1", "missing") is None
            with pytest.raises(RepositoryConflictError, match="another Project"):
                repository.active_ref("another", "variant-1")
        assert all("payload_json" not in item.lower() for item in statements)
        updated = revised_draft(draft, digest_context="edited")
        repository.update_draft(updated, expected_revision=0)
        assert repository.active_ref("project-1", "variant-1") == updated.ref
        repository.discard_draft("project-1", "variant-1", expected_revision=1)
        assert repository.active_ref("project-1", "variant-1") is None
    finally:
        repository.close()


@pytest.mark.parametrize("change", ["identity", "revision", "digest"])
def test_competing_connection_changes_are_rejected_after_snapshot_preparation(tmp_path, change) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    other = None
    try:
        line = _line()
        original = _empty_draft("draft-1", line)
        repository.create_draft(original)
        expected = DraftWriteExpectation.from_ref(repository.active_ref("project-1", "variant-1"), line)
        proposed = revised_draft(original, digest_context="prepared")
        other = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
        if change == "identity":
            other.discard_draft("project-1", "variant-1", expected_revision=0)
            current = _empty_draft("replacement", line)
            other.create_draft(current)
        elif change == "revision":
            current = revised_draft(original, digest_context="other writer")
            other.update_draft(current, expected_revision=0)
        else:
            current = replace(original, ref=replace(original.ref, decision_set_digest="other-digest"))
            with other.transaction():
                other._connection.execute(
                    "UPDATE drafts SET decision_set_digest = ?, payload_json = ?",
                    (current.ref.decision_set_digest, dumps(current)),
                )
        with pytest.raises((RepositoryConflictError, RevisionConflictError)):
            repository.draft_transactions(_LineReader(line)).save_draft(proposed, expectation=expected)
        assert repository.active_draft("project-1", "variant-1") == current
        assert repository.active_ref("project-1", "variant-1") == current.ref
    finally:
        if other is not None:
            other.close()
        repository.close()


@pytest.mark.parametrize(
    "column, value",
    [
        ("draft_id", "another"),
        ("base_version_id", "another-version"),
        ("base_content_digest", "another-base-digest"),
        ("revision", 99),
        ("decision_set_digest", "another-decision-digest"),
    ],
)
def test_reused_current_snapshot_still_uses_complete_sql_compare_and_swap(tmp_path, column, value) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        original = _empty_draft("draft-1", _line())
        repository.create_draft(original)
        proposed = revised_draft(original, digest_context="prepared")
        with pytest.raises(RepositoryConflictError, match="identity changed before update"):
            with repository.transaction():
                repository._connection.execute(f"UPDATE drafts SET {column} = ?", (value,))
                repository._drafts.update_current(original, proposed)
        assert repository.active_draft("project-1", "variant-1") == original
        assert repository.active_ref("project-1", "variant-1") == original.ref
    finally:
        repository.close()


def test_reused_current_snapshot_cannot_write_outside_a_transaction(tmp_path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        original = _empty_draft("draft-1", _line())
        repository.create_draft(original)
        with pytest.raises(RuntimeError, match="write transaction"):
            repository._drafts.update_current(original, revised_draft(original))
        assert repository.active_ref("project-1", "variant-1") == original.ref
    finally:
        repository.close()
