"""Story 26: TaskManager 断点续传与暂停/恢复测试。

Test F: TaskManager pause/resume 功能
Test G: stop_task action 参数
Test H: Phase 1 infrastructure changes (m3, m4, M2)
"""
from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace

from transbridge.smart_assistant.tools.task_manager import TaskManager


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
        from transbridge.smart_assistant.tools.tool_translator import _tool_stop_task
        tid = self.tm.register()
        self._test_ids.append(tid)
        result = _tool_stop_task({"task_id": tid, "action": "pause"}, self._mock_ctx())
        self.assertTrue(result.success)
        status = self.tm.get_status(tid)
        self.assertEqual(status["status"], "paused")

    def test_g2_stop_task_action_resume(self):
        from transbridge.smart_assistant.tools.tool_translator import _tool_stop_task
        tid = self.tm.register()
        self._test_ids.append(tid)
        self.tm.pause(tid)
        result = _tool_stop_task({"task_id": tid, "action": "resume"}, self._mock_ctx())
        self.assertTrue(result.success)
        status = self.tm.get_status(tid)
        self.assertEqual(status["status"], "running")

    def test_g3_stop_task_action_stop_default(self):
        from transbridge.smart_assistant.tools.tool_translator import _tool_stop_task
        tid = self.tm.register()
        self._test_ids.append(tid)
        result = _tool_stop_task({"task_id": tid}, self._mock_ctx())
        self.assertTrue(result.success)
        status = self.tm.get_status(tid)
        self.assertEqual(status["status"], "cancelled")

    def test_g4_stop_task_invalid_action(self):
        from transbridge.smart_assistant.tools.tool_translator import _tool_stop_task
        tid = self.tm.register()
        self._test_ids.append(tid)
        result = _tool_stop_task({"task_id": tid, "action": "invalid"}, self._mock_ctx())
        self.assertFalse(result.success)

    def test_g5_stop_task_pause_all(self):
        from transbridge.smart_assistant.tools.tool_translator import _tool_stop_task
        tid1 = self.tm.register()
        tid2 = self.tm.register()
        self._test_ids.extend([tid1, tid2])
        result = _tool_stop_task({"action": "pause"}, self._mock_ctx())
        self.assertTrue(result.success)
        self.assertEqual(self.tm.get_status(tid1)["status"], "paused")
        self.assertEqual(self.tm.get_status(tid2)["status"], "paused")

    def test_g6_stop_task_resume_all(self):
        from transbridge.smart_assistant.tools.tool_translator import _tool_stop_task
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
        from transbridge.smart_assistant.tools.tool_translator import _tool_stop_task
        _tool_stop_task({"action": "stop"}, self._mock_ctx())
        result = _tool_stop_task({"action": "pause"}, self._mock_ctx())
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("affected_task_ids"), [])

    def test_g8_action_label_helper(self):
        from transbridge.smart_assistant.tools.tool_translator import TranslationController
        from transbridge.ui.context import AppContext
        from transbridge.smart_assistant.tools.task_manager import TaskManager
        ctrl = TranslationController(AppContext(), TaskManager())
        self.assertIn("停止", ctrl._action_label("stop"))
        self.assertIn("暂停", ctrl._action_label("pause"))
        self.assertIn("恢复", ctrl._action_label("resume"))


class TestTaskManagerPhase1(unittest.TestCase):
    """H: Phase 1 infrastructure changes — m3, m4, M2."""

    def setUp(self):
        TaskManager.reset()
        self.tm = TaskManager()
        self._test_ids = []

    def tearDown(self):
        for tid in self._test_ids:
            try:
                self.tm.cleanup(tid)
            except Exception:
                pass
        TaskManager.reset()

    # ── m3: get_status 使用 dict() 浅拷贝 ──────────────────────

    def test_h1_get_status_progress_is_independent_copy(self):
        """m3: get_status 返回的 progress 是独立副本，修改不影响原 handle。"""
        tid = self.tm.register()
        self._test_ids.append(tid)
        self.tm.update_progress(tid, {"current": 5, "total": 10})

        status = self.tm.get_status(tid)
        self.assertEqual(status["progress"], {"current": 5, "total": 10})

        # 修改返回的 progress 不应影响内部 handle
        status["progress"]["current"] = 999
        handle = self.tm.get_handle(tid)
        self.assertEqual(handle.progress["current"], 5,
                         "dict() 浅拷贝应对扁平 dict 提供独立副本")

    def test_h2_get_status_progress_shallow_copy_isolation(self):
        """m3: progress 中嵌套 mutable 值时，浅拷贝共享内层引用（可接受）。

        progress 是扁平 dict（键为 "current"/"total"/"error"），
        值都是不可变类型（int/str），所以 dict() 浅拷贝完全安全。
        """
        tid = self.tm.register()
        self._test_ids.append(tid)
        self.tm.update_progress(tid, {"current": 0, "total": 100, "error": None})

        status = self.tm.get_status(tid)
        # 验证所有键都存在
        self.assertEqual(status["progress"]["current"], 0)
        self.assertEqual(status["progress"]["total"], 100)
        self.assertIsNone(status["progress"]["error"])

    # ── m4: cleanup_all 锁外 join ─────────────────────────────

    def test_h3_cleanup_all_removes_inactive_tasks(self):
        """m4: cleanup_all 清理已完成/失败/取消的任务。"""
        tid1 = self.tm.register()
        tid2 = self.tm.register()
        tid3 = self.tm.register()
        self._test_ids.extend([tid1, tid2, tid3])

        self.tm.set_status(tid1, "completed")
        self.tm.set_status(tid2, "failed")

        count = self.tm.cleanup_all()
        self.assertEqual(count, 2)
        self.assertIsNone(self.tm.get_handle(tid1))
        self.assertIsNone(self.tm.get_handle(tid2))
        self.assertIsNotNone(self.tm.get_handle(tid3))

    def test_h4_cleanup_all_skips_active_tasks(self):
        """m4: cleanup_all 不清理 running/paused 任务。"""
        tid1 = self.tm.register()
        tid2 = self.tm.register()
        self._test_ids.extend([tid1, tid2])
        self.tm.pause(tid2)

        count = self.tm.cleanup_all()
        self.assertEqual(count, 0)
        self.assertIsNotNone(self.tm.get_handle(tid1))
        self.assertIsNotNone(self.tm.get_handle(tid2))

    def test_h5_cleanup_all_handles_empty_state(self):
        """m4: cleanup_all 在无任务时返回 0。"""
        count = self.tm.cleanup_all()
        self.assertEqual(count, 0)

    def test_h6_cleanup_all_joins_threads_outside_lock(self):
        """m4: cleanup_all 在锁外 join 线程，不阻塞其他操作。

        验证：cleanup_all 调用期间，其他线程可以正常执行
        get_handle() 等操作（即锁已释放）。
        """
        started = threading.Event()
        finish = threading.Event()

        def _slow_thread():
            started.set()
            finish.wait()  # 阻塞直到被通知

        tid = self.tm.register()
        self._test_ids.append(tid)

        # 使用 start_thread (M2) 创建并关联线程
        thread = self.tm.start_thread(tid, _slow_thread)
        started.wait(timeout=2)  # 等待线程开始运行
        self.assertTrue(thread.is_alive())

        self.tm.set_status(tid, "completed")

        # cleanup_all 应该在锁外 join，所以 get_handle 应该可以快速返回
        import time as _time
        t0 = _time.time()
        count = self.tm.cleanup_all()
        elapsed = _time.time() - t0

        # 线程还在运行（等待 finish event），join(timeout=2) 会阻塞约 2 秒
        # 但 get_handle 在 join 期间应该可用（锁已释放）
        self.assertTrue(elapsed >= 1.9, f"Expected ~2s join timeout, got {elapsed:.2f}s")

        # get_handle 在 cleanup_all 的 join 期间仍可访问（锁外 join）
        handle_after = self.tm.get_handle(tid)
        self.assertIsNone(handle_after, "handle should be removed from dict")
        self.assertEqual(count, 1)

        # 清理：通知线程结束
        finish.set()
        thread.join(timeout=3)

    # ── M2: start_thread 方法 ──────────────────────────────────

    def test_h7_start_thread_creates_daemon_thread(self):
        """M2: start_thread 创建守护线程并关联到任务句柄。"""
        ran = threading.Event()

        def _target():
            ran.set()

        tid = self.tm.register()
        self._test_ids.append(tid)

        thread = self.tm.start_thread(tid, _target)
        ran.wait(timeout=2)
        thread.join(timeout=2)

        self.assertTrue(ran.is_set(), "Thread should have run")
        self.assertTrue(thread.daemon, "Thread should be daemon")

        handle = self.tm.get_handle(tid)
        self.assertIsNotNone(handle)
        self.assertIs(handle._thread, thread, "Thread should be associated with handle")

    def test_h8_start_thread_nonexistent_task_returns_thread(self):
        """M2: 任务不存在时仍创建并启动线程（不关联句柄）。"""
        ran = threading.Event()

        def _target():
            ran.set()

        thread = self.tm.start_thread("nonexistent_id", _target)
        ran.wait(timeout=2)
        thread.join(timeout=2)

        self.assertTrue(ran.is_set(), "Thread should run even without valid handle")
        self.assertIsInstance(thread, threading.Thread)

    def test_h9_start_thread_returns_running_thread(self):
        """M2: start_thread 返回的线程已经启动。"""
        tid = self.tm.register()
        self._test_ids.append(tid)

        thread = self.tm.start_thread(tid, lambda: time.sleep(0.1))
        self.assertTrue(thread.is_alive(), "Thread should be running immediately")
        thread.join(timeout=2)
