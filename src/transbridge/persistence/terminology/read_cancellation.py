# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Interrupt a locked, read-only SQLite operation using its query token."""

from contextlib import contextmanager
import sqlite3


@contextmanager
def cancellable_read(connection, cancellation):
    """Caller owns the connection lock; never install this around writes."""
    if cancellation is None:
        yield
        return
    cancellation.raise_if_cancelled()
    connection.set_progress_handler(lambda: int(cancellation.is_cancelled), 1000)
    try:
        yield
        cancellation.raise_if_cancelled()
    except sqlite3.OperationalError:
        cancellation.raise_if_cancelled()
        raise
    finally:
        connection.set_progress_handler(None, 0)


def checked_rows(rows, cancellation):
    for row in rows:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        yield row
