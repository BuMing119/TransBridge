"""A competing Session write is reloaded before the command is reduced again."""

from dataclasses import replace

import pytest
from test_session_lifecycle import _context, _Repository, _snapshot

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.sessions.commands import save_command


def test_conflicting_command_preserves_the_competing_change():
    original = _snapshot("session-a")
    latest = replace(original, revision=3, backend_summary="other writer")
    repository = _Repository([latest])
    saves = []

    def save(snapshot, *, expected_revision, context):
        saves.append(expected_revision)
        if len(saves) == 1:
            raise DomainError(ErrorCategory.CONFLICT, "SESSION_REVISION_CONFLICT", "changed")
        repository.snapshots["session-a"] = snapshot
        return snapshot

    repository.save = save
    result = save_command(
        repository,
        original,
        2,
        _context("session-a"),
        lambda current: replace(current, name="renamed"),
        retry_conflict=True,
    )
    assert saves == [2, 3]
    assert result.revision == 4
    assert result.name == "renamed"
    assert result.backend_summary == "other writer"


def test_unsaved_local_changes_do_not_get_discarded_by_reload():
    original = _snapshot("session-a")
    repository = _Repository([original])

    def save(*args, **kwargs):
        raise DomainError(ErrorCategory.CONFLICT, "SESSION_REVISION_CONFLICT", "changed")

    repository.save = save
    with pytest.raises(DomainError):
        save_command(repository, original, 1, _context("session-a"), lambda current: current, retry_conflict=False)
    assert repository.load_calls == []
