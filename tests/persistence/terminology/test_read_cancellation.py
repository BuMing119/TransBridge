import sqlite3

import pytest

from transbridge.application.terminology.query_control import QueryCancellation, QueryCancelled
from transbridge.persistence.terminology.read_cancellation import cancellable_read


def test_cancel_interrupts_sql_and_restores_connection_for_following_reads():
    connection = sqlite3.connect(":memory:")
    token = QueryCancellation()
    seen = []

    def observe(value):
        seen.append(value)
        if value == 100:
            token.cancel()
        return value

    connection.create_function("observe", 1, observe)
    try:
        with pytest.raises(QueryCancelled), cancellable_read(connection, token):
            connection.execute(
                "WITH RECURSIVE numbers(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM numbers WHERE x<100000) "
                "SELECT SUM(observe(x)) FROM numbers"
            ).fetchone()
        assert 100 <= len(seen) < 1000
        assert connection.execute("SELECT 42").fetchone() == (42,)
        with pytest.raises(sqlite3.OperationalError), cancellable_read(connection, QueryCancellation()):
            connection.execute("SELECT * FROM missing_table")
    finally:
        connection.close()
