"""Compare-and-save commands with one reload for an externally advanced Session."""

from dataclasses import replace

from transbridge.application.contracts import DomainError


def save_command(repository, snapshot, expected_revision, context, update, *, retry_conflict):
    for attempt in range(2):
        updated = update(snapshot)
        if updated.ref != snapshot.ref or updated.owner != snapshot.owner:
            raise ValueError("Session command cannot change its identity or owner")
        if updated == snapshot and snapshot.revision == expected_revision:
            return snapshot
        updated = replace(updated, revision=snapshot.revision + 1)
        try:
            return repository.save(updated, expected_revision=expected_revision, context=context)
        except DomainError as exc:
            if attempt or not retry_conflict or exc.code != "SESSION_REVISION_CONFLICT":
                raise
            latest = repository.load(snapshot.ref, context)
            if latest.owner != snapshot.owner:
                raise ValueError("Session ownership changed during command retry") from exc
            snapshot = latest
            expected_revision = latest.revision
    raise AssertionError("Session command retry exhausted")
