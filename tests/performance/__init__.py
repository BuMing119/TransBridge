"""Release S03 performance / stability regression gates.

These modules freeze the confirmed performance and stability budgets as fixed
corpus + hardware tier + P50/P95 tests with versioned threshold configuration.

Development-machine results are treated as early feedback only; the hard
Windows hardware evidence is produced by release S05 in a dedicated
environment. Thresholds live here as versioned constants and may only be
relaxed through requirement confirmation (never by silently weakening a test
to make it pass).
"""
