"""Fixtures specific to smart_assistant tests.

Re-exports shared fixtures from tests.conftest for convenience.
"""

# Ensure root conftest fixtures are discoverable
from tests.conftest import (
    make_entry,
    make_llm_config,
    make_test_collection,
    MockAppContext,
    MockSignal,
    MockToolSpec,
)
