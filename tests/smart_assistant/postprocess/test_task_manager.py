"""Story 26: TaskManager 断点续传与暂停/恢复测试。

Test F: TaskManager pause/resume 功能
Test G: stop_task action 参数
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.transbridge.smart_assistant.tools.task_manager import TaskManager


class TestTaskManagerPauseResume(unittest.TestCase):
    """F: TaskManager pause/resume 功能测试。"""

    def setUp(self):
        self.tm = TaskManager()
        self._test_ids = []

    def tearDown(self):
        for tid in self._test_ids:
            try:
                self.tm.cleanup(tid)
            except Exception:
                pass

    def test_f1_pause_sets_status_paused(self):
        tid = self.tm.register()
        self._test_ids.append(tid)
        self.assertTrue(self.tm.pause(tid))
        status = self.tm.get_status(tid)
        self.assertEqual(status["status"], "paused")

    def test_f2_resume_sets_status_running(self):
        tid = self.tm.register()
        self._test_ids.append(tid)
        self.tm.pause(tid)
        self.assertTrue(self.tm.resume(tid))
        status = self.tm.get_status(tid)
        self.assertEqual(status["status"], "running")

    def test_f3_pause_nonexistent_returns_false(self):
        self.assertFalse(self.tm.pause("nonexistent"))

    def test_f4_resume_nonexistent_returns_false(self):
        self.assertFalse(self.tm.resume("nonexistent"))

    def test_f5_list_active_includes_paused(self):
        tid = self.tm.register()
        self._test_ids.append(tid)
        self.tm.pause(tid)
        active = self.tm.list_active()
        self.assertIn(tid, active)

    def test_f6_list_active_excludes_completed(self):
        tid = self.tm.register()
        self._test_ids.append(tid)
        self.tm.set_status(tid, "completed")
        active = self.tm.list_active()
        self.assertNotIn(tid, active)

    def test_f7_register_creates_pause_event(self):
        tid = self.tm.register()
        self._test_ids.append(tid)
        handle = self.tm.get_handle(tid)
        self.assertIsNotNone(handle)
        self.assertIsNotNone(handle.pause_event)
        self.assertTrue(handle.pause_event.is_set())

    def test_f8_cancel_wakes_paused_task(self):
        tid = self.tm.register()
        self._test_ids.append(tid)
        self.tm.pause(tid)
        self.assertTrue(self.tm.cancel(tid))
        handle = self.tm.get_handle(tid)
        self.assertTrue(handle.pause_event.is_set())
        self.assertEqual(handle.status, "cancelled")

    def test_f9_pause_creates_event_if_none(self):
        tid = self.tm.register()
        self._test_ids.append(tid)
        handle = self.tm.get_handle(tid)
        handle.pause_event = None
        self.assertTrue(self.tm.pause(tid))
        self.assertIsNotNone(handle.pause_event)
        self.assertFalse(handle.pause_event.is_set())

    def test_f10_resume_creates_event_if_none(self):
        tid = self.tm.register()
        self._test_ids.append(tid)
        handle = self.tm.get_handle(tid)
        handle.pause_event = None
        self.assertTrue(self.tm.resume(tid))
        self.assertIsNotNone(handle.pause_event)
        self.assertTrue(handle.pause_event.is_set())


class TestStopTaskActionParameter(unittest.TestCase):
    """G: stop_task action 参数测试。"""

    def setUp(self):
        self.tm = TaskManager()
        self._test_ids = []

    def tearDown(self):
        for tid in self._test_ids:
            try:
                self.tm.cleanup(tid)
            except Exception:
                pass

    def _mock_ctx(self):
        return SimpleNamespace(collection=None, esp_path=None, config=None,
                               translation_scope={}, safe_mutate=lambda fn: fn())

    def test_g1_stop_task_action_pause(self):
        from src.transbridge.smart_assistant.tools.tool_translator import _tool_stop_task
        tid = self.tm.register()
        self._test_ids.append(tid)
        result = _tool_stop_task({"task_id": tid, "action": "pause"}, self._mock_ctx())
        self.assertTrue(result.success)
        status = self.tm.get_status(tid)
        self.assertEqual(status["status"], "paused")

    def test_g2_stop_task_action_resume(self):
        from src.transbridge.smart_assistant.tools.tool_translator import _tool_stop_task
        tid = self.tm.register()
        self._test_ids.append(tid)
        self.tm.pause(tid)
        result = _tool_stop_task({"task_id": tid, "action": "resume"}, self._mock_ctx())
        self.assertTrue(result.success)
        status = self.tm.get_status(tid)
        self.assertEqual(status["status"], "running")

    def test_g3_stop_task_action_stop_default(self):
        from src.transbridge.smart_assistant.tools.tool_translator import _tool_stop_task
        tid = self.tm.register()
        self._test_ids.append(tid)
        result = _tool_stop_task({"task_id": tid}, self._mock_ctx())
        self.assertTrue(result.success)
        status = self.tm.get_status(tid)
        self.assertEqual(status["status"], "cancelled")

    def test_g4_stop_task_invalid_action(self):
        from src.transbridge.smart_assistant.tools.tool_translator import _tool_stop_task
        tid = self.tm.register()
        self._test_ids.append(tid)
        result = _tool_stop_task({"task_id": tid, "action": "invalid"}, self._mock_ctx())
        self.assertFalse(result.success)

    def test_g5_stop_task_pause_all(self):
        from src.transbridge.smart_assistant.tools.tool_translator import _tool_stop_task
        tid1 = self.tm.register()
        tid2 = self.tm.register()
        self._test_ids.extend([tid1, tid2])
        result = _tool_stop_task({"action": "pause"}, self._mock_ctx())
        self.assertTrue(result.success)
        self.assertEqual(self.tm.get_status(tid1)["status"], "paused")
        self.assertEqual(self.tm.get_status(tid2)["status"], "paused")

    def test_g6_stop_task_resume_all(self):
        from src.transbridge.smart_assistant.tools.tool_translator import _tool_stop_task
        tid1 = self.tm.register()
        tid2 = self.tm.register()
        self._test_ids.extend([tid1, tid2])
        self.tm.pause(tid1)
        self.tm.pause(tid2)
        result = _tool_stop_task({"action": "resume"}, self._mock_ctx())
        self.assertTrue(result.success)
        self.assertEqual(self.tm.get_status(tid1)["status"], "running")
        self.assertEqual(self.tm.get_status(tid2)["status"], "running")

    def test_g7_stop_task_no_active_tasks_empty_state(self):
        from src.transbridge.smart_assistant.tools.tool_translator import _tool_stop_task
        _tool_stop_task({"action": "stop"}, self._mock_ctx())
        result = _tool_stop_task({"action": "pause"}, self._mock_ctx())
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("affected_task_ids"), [])

    def test_g8_action_label_helper(self):
        from src.transbridge.smart_assistant.tools.tool_translator import _action_label
        self.assertIn("停止", _action_label("stop"))
        self.assertIn("暂停", _action_label("pause"))
        self.assertIn("恢复", _action_label("resume"))
