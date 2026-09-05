"""Tests for base.py infrastructure: _apply_after_guards (m5) and resolve_scope_to_entry_ids (M3)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from tests.conftest import MockAppContext, make_test_collection
from transbridge.smart_assistant.execution_engine import StepResult
from transbridge.smart_assistant.guardrails.base import GuardResult

# Module-level imports to avoid bound-method descriptor wrapping
from transbridge.smart_assistant.tools.base import _apply_after_guards, resolve_scope_to_entry_ids


# ============================================================
# Test_apply_after_guards (m5: deduplication helper)
# ============================================================
class TestApplyAfterGuards(unittest.TestCase):
    """Test the extracted _apply_after_guards helper function."""

    def setUp(self):
        self.step = {"tool": "test_tool", "args": {"key": "value"}}
        self.ctx = MagicMock()

    # ── helper ──
    def _make_guard(self, allowed=True, modified_result=None, reason=""):
        """Create a mock guard middleware with configurable after_execute behavior."""
        guard = MagicMock()
        guard.after_execute.return_value = GuardResult(
            allowed=allowed,
            reason=reason,
            modified_result=modified_result,
        )
        return guard

    # ── normal flow ──

    def test_normal_flow_returns_step_result(self):
        """All guards allow -> returns (StepResult, None)."""
        g1 = self._make_guard(allowed=True)
        g2 = self._make_guard(allowed=True)
        guards = [g1, g2]

        sr, rejection = _apply_after_guards(guards, self.step, "test_tool", True, "ok", {"a": 1}, self.ctx)

        self.assertIsNone(rejection)
        self.assertIsInstance(sr, StepResult)
        self.assertTrue(sr.success)
        self.assertEqual(sr.message, "ok")
        self.assertEqual(sr.data, {"a": 1})
        self.assertEqual(sr.tool, "test_tool")

    def test_single_guard_called(self):
        """Single guard is called with correct args."""
        g1 = self._make_guard(allowed=True)
        guards = [g1]

        _apply_after_guards(guards, self.step, "test_tool", True, "ok", {"a": 1}, self.ctx)

        g1.after_execute.assert_called_once()
        call_args = g1.after_execute.call_args[0]
        self.assertIs(call_args[0], self.step)
        self.assertIs(call_args[2], self.ctx)

    # ── reverse order (onion model) ──

    def test_guards_applied_in_reverse_order(self):
        """Guards should be applied in reverse order (onion model)."""
        call_order = []

        def _make_tracking_guard(name):
            guard = MagicMock()

            def _after(step, result, ctx):
                call_order.append(name)
                return GuardResult(allowed=True)

            guard.after_execute = _after
            return guard

        g1 = _make_tracking_guard("g1")
        g2 = _make_tracking_guard("g2")
        g3 = _make_tracking_guard("g3")
        guards = [g1, g2, g3]

        _apply_after_guards(guards, self.step, "test_tool", True, "ok", None, self.ctx)

        # Onion model: g3 first, then g2, then g1
        self.assertEqual(call_order, ["g3", "g2", "g1"])

    # ── rejection ──

    def test_rejection_returns_structured_guard_result(self):
        """When a guard rejects, preserve its structured diagnostics."""
        g1 = self._make_guard(allowed=True)
        g2 = self._make_guard(allowed=False, reason="sensitive data detected")
        guards = [g1, g2]

        sr, rejection = _apply_after_guards(
            guards, self.step, "test_tool", True, "ok", {"phone": "13800138000"}, self.ctx
        )

        self.assertEqual(rejection.reason, "sensitive data detected")
        self.assertIsNotNone(sr)

    def test_rejection_stops_further_guards(self):
        """When a guard rejects, subsequent guards are NOT called."""
        g1 = self._make_guard(allowed=True)
        g2 = self._make_guard(allowed=False, reason="blocked")
        g3 = self._make_guard(allowed=True)
        guards = [g1, g2, g3]

        _apply_after_guards(guards, self.step, "test_tool", True, "ok", {}, self.ctx)

        # Reverse order: g3 first (allowed), g2 second (rejects) -> g1 never called
        g3.after_execute.assert_called_once()
        g2.after_execute.assert_called_once()
        g1.after_execute.assert_not_called()

    # ── data modification ──

    def test_guard_can_modify_result_data(self):
        """A guard can set modified_result to alter the data passed downstream."""
        g1 = self._make_guard(allowed=True)
        g2 = self._make_guard(allowed=True, modified_result={"sanitized": True})
        guards = [g1, g2]

        sr, rejection = _apply_after_guards(guards, self.step, "test_tool", True, "ok", {"raw": "data"}, self.ctx)

        self.assertIsNone(rejection)
        # g2 runs first (reverse), modifies data; g1 runs second, sees modified data
        self.assertEqual(sr.data, {"sanitized": True})

    def test_multiple_guards_chaining_modifications(self):
        """Each guard sees the result of the previous guard's modifications."""

        def _append_guard(key, value):
            guard = MagicMock()

            def _after(step, result, ctx):
                d = dict(result.data) if result.data else {}
                d[key] = value
                return GuardResult(allowed=True, modified_result=d)

            guard.after_execute = _after
            return guard

        g1 = _append_guard("by_g1", 1)
        g2 = _append_guard("by_g2", 2)
        g3 = _append_guard("by_g3", 3)
        guards = [g1, g2, g3]

        sr, rejection = _apply_after_guards(guards, self.step, "test_tool", True, "ok", {"initial": 0}, self.ctx)

        self.assertIsNone(rejection)
        # Reverse order: g3 -> g2 -> g1
        self.assertEqual(sr.data, {"initial": 0, "by_g3": 3, "by_g2": 2, "by_g1": 1})

    def test_none_data_passed_through(self):
        """data=None should be passed through correctly."""
        g1 = self._make_guard(allowed=True)
        guards = [g1]

        sr, rejection = _apply_after_guards(guards, self.step, "test_tool", True, "ok", None, self.ctx)

        self.assertIsNone(rejection)
        self.assertIsNone(sr.data)

    def test_empty_guards_list(self):
        """Empty guards list -> returns (StepResult, None) immediately."""
        sr, rejection = _apply_after_guards([], self.step, "test_tool", True, "ok", {"a": 1}, self.ctx)

        self.assertIsNone(rejection)
        self.assertEqual(sr.data, {"a": 1})
        self.assertEqual(sr.tool, "test_tool")

    # ── failure case ──

    def test_failure_success_false(self):
        """When tool failed, success=False is preserved through guards."""
        g1 = self._make_guard(allowed=True)
        guards = [g1]

        sr, rejection = _apply_after_guards(
            guards, self.step, "test_tool", False, "something went wrong", None, self.ctx
        )

        self.assertIsNone(rejection)
        self.assertFalse(sr.success)
        self.assertEqual(sr.message, "something went wrong")


# ============================================================
# TestResolveScopeToEntryIds (M3: scope -> entry_ids)
# ============================================================
class TestResolveScopeToEntryIds(unittest.TestCase):
    """Test resolve_scope_to_entry_ids public function."""

    def test_valid_scope_with_stages_returns_filtered_entry_ids(self):
        """Scope with stages should filter collection and return matching entry keys."""
        ctx = MockAppContext(make_test_collection(10))
        ctx.translation_scope = {
            "stages": [0],
            "labels": [],
            "categories": [],
            "action": "include",
        }

        result = resolve_scope_to_entry_ids(ctx, ctx.collection)

        self.assertIsNotNone(result)
        expected = [e.key for e in ctx.collection if e.stage == 0]
        self.assertEqual(sorted(result), sorted(expected))

    def test_valid_scope_with_categories_returns_filtered_ids(self):
        """Scope with categories should filter by context prefix."""
        ctx = MockAppContext(make_test_collection(10))
        ctx.translation_scope = {
            "stages": [],
            "labels": [],
            "categories": ["INFO"],
            "action": "include",
        }

        result = resolve_scope_to_entry_ids(ctx, ctx.collection)

        self.assertIsNotNone(result)
        expected = [e.key for e in ctx.collection if e.context and e.context.startswith("INFO:")]
        self.assertEqual(sorted(result), sorted(expected))

    def test_empty_scope_returns_none(self):
        """Empty scope (all lists empty) -> returns None."""
        ctx = MockAppContext(make_test_collection(10))
        ctx.translation_scope = {
            "stages": [],
            "labels": [],
            "categories": [],
            "action": "include",
        }

        result = resolve_scope_to_entry_ids(ctx, ctx.collection)

        self.assertIsNone(result)

    def test_missing_translation_scope_returns_none(self):
        """ctx without translation_scope attribute -> returns None."""
        from types import SimpleNamespace

        ctx_no_scope = SimpleNamespace()
        # Note: resolve_scope_to_entry_ids also accesses ctx.collection via arg,
        # but the function only uses ctx for translation_scope and entry_labels
        setattr(ctx_no_scope, "something_else", True)

        result = resolve_scope_to_entry_ids(ctx_no_scope, make_test_collection(5))

        self.assertIsNone(result)

    def test_none_scope_returns_none(self):
        """translation_scope attribute exists but value is falsy -> returns None."""
        from types import SimpleNamespace

        collection = make_test_collection(5)
        ctx = SimpleNamespace()
        ctx.collection = collection
        ctx.translation_scope = None  # explicitly None
        ctx.entry_labels = None

        result = resolve_scope_to_entry_ids(ctx, collection)

        self.assertIsNone(result)

    def test_scope_with_only_labels_and_entry_labels(self):
        """Scope with labels and matching entry_labels should filter correctly."""
        collection = make_test_collection(6)
        ctx = MockAppContext(collection)
        ctx.translation_scope = {
            "stages": [],
            "labels": ["npc"],
            "categories": [],
            "action": "include",
        }
        ctx._entry_labels = {
            "entry_000": {"npc", "quest"},
            "entry_001": {"item"},
            "entry_002": {"npc"},
        }

        result = resolve_scope_to_entry_ids(ctx, ctx.collection)

        self.assertIsNotNone(result)
        self.assertEqual(sorted(result), ["entry_000", "entry_002"])

    def test_scope_all_empty_strings_returns_none(self):
        """Scope keys present but all empty strings/lists -> returns None."""
        ctx = MockAppContext(make_test_collection(10))
        ctx.translation_scope = {
            "stages": [],
            "labels": [],
            "categories": [],
            "action": "include",
        }

        result = resolve_scope_to_entry_ids(ctx, ctx.collection)

        self.assertIsNone(result)

    def test_scope_stages_and_categories_combined(self):
        """Combined stages + categories scope -> intersection filter."""
        ctx = MockAppContext(make_test_collection(10))
        ctx.translation_scope = {
            "stages": [0, 1],
            "labels": [],
            "categories": ["NPC_"],
            "action": "include",
        }

        result = resolve_scope_to_entry_ids(ctx, ctx.collection)

        self.assertIsNotNone(result)
        expected = [e.key for e in ctx.collection if e.stage in (0, 1) and e.context and e.context.startswith("NPC_:")]
        self.assertEqual(sorted(result), sorted(expected))


if __name__ == "__main__":
    unittest.main()
