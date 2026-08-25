"""TaskMonitorWidget 单元测试 — 核心组件与数据流验证。

FR14 Story 01: TaskMonitorWidget 核心组件测试。
"""

from __future__ import annotations

import time

from PyQt6.QtWidgets import QApplication, QPushButton
import pytest

from transbridge.ui.tools.smart_assistant.task_monitor import (
    _STATUS_COLORS,
    _STATUS_LABELS,
    TaskMonitorWidget,
    _TaskCard,
)


@pytest.fixture
def qapp():
    """确保 QApplication 存在。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def monitor(qapp):
    w = TaskMonitorWidget()
    w.show()  # 需要 show() 子控件 isVisible() 才返回 True
    return w


def _make_task(task_id, status, current=0, total=0, meta_type="翻译", age=30):
    return {
        "task_id": task_id,
        "status": status,
        "progress": {"current": current, "total": total},
        "metadata": {"type": meta_type},
        "created_at": time.time() - age,
    }


class TestTaskMonitorWidget:
    """TaskMonitorWidget 主控件测试。"""

    def test_initial_empty_state(self, monitor):
        """初始状态：标题显示 0 个任务，空状态保留但任务区默认折叠。"""
        assert "后台任务 (0)" in monitor._title_label.text()
        assert not monitor._empty_label.isHidden()
        assert monitor._collapsed
        assert not monitor._scroll.isVisible()
        assert monitor.maximumHeight() == 36

    def test_refresh_with_tasks(self, monitor):
        """refresh() 渲染任务卡片并更新标题和按钮。"""
        tasks = [
            _make_task("t1", "running", 50, 100, meta_type="翻译"),
            _make_task("t2", "completed", 100, 100, meta_type="润色"),
        ]
        monitor.refresh(tasks)

        assert not monitor._empty_label.isVisible()
        assert "后台任务 (2)" in monitor._title_label.text()
        # 折叠时不抢占空间；展开后已完成任务可清理。
        assert not monitor._clear_all_btn.isVisible()
        monitor._toggle_collapse()
        assert monitor._clear_all_btn.isVisible()

    def test_refresh_empty_clears_cards(self, monitor):
        """refresh([]) 清除卡片并显示空状态。"""
        monitor.refresh([_make_task("t1", "running")])
        assert not monitor._empty_label.isVisible()

        monitor.refresh([])
        assert "后台任务 (0)" in monitor._title_label.text()
        assert not monitor._empty_label.isHidden()

    def test_reset_clears_everything(self, monitor):
        """reset() 清空所有任务。"""
        monitor.refresh([_make_task("t1", "running")])
        monitor.reset()
        assert not monitor._empty_label.isHidden()

    def test_collapse_toggle(self, monitor):
        """折叠按钮切换 scroll 可见性。"""
        assert not monitor._scroll.isVisible()
        assert monitor._collapsed
        assert monitor.maximumHeight() == 36

        monitor._toggle_collapse()
        assert monitor._scroll.isVisible()
        assert not monitor._collapsed
        assert monitor.minimumHeight() == 140
        assert monitor.maximumHeight() == 300
        assert not monitor._collapse_btn.icon().isNull()

        monitor._toggle_collapse()
        assert not monitor._scroll.isVisible()
        assert monitor._collapsed
        assert monitor.minimumHeight() == 36
        assert monitor.maximumHeight() == 36

    def test_clear_all_button_emits_signal(self, monitor):
        """'清除已完成'按钮发射 cleanup_completed 信号。"""
        # 需要先有非活跃任务才能显示按钮
        monitor.refresh([_make_task("t1", "completed")])

        signals = []
        monitor.task_action.connect(lambda tid, action: signals.append((tid, action)))

        monitor._clear_all_btn.click()
        assert len(signals) == 1
        assert signals[0] == ("__all__", "cleanup_completed")

    def test_no_clear_button_with_all_active(self, monitor):
        """全部为活跃任务时不显示'清除已完成'按钮。"""
        monitor.refresh([
            _make_task("t1", "running"),
            _make_task("t2", "paused"),
        ])
        assert not monitor._clear_all_btn.isVisible()


class TestTaskCard:
    """_TaskCard 单任务卡片测试。"""

    def test_running_card_labels(self, qapp):
        """运行中任务卡片：名称、状态标签正确。"""
        card = _TaskCard("t1", _make_task("t1", "running", 30, 100, meta_type="翻译"))
        card.show()
        assert card._name_label.text() == "翻译"
        assert card._status_label.text() == "运行中"

    def test_running_card_has_buttons(self, qapp):
        """运行中任务有暂停和取消按钮。"""
        card = _TaskCard("t1", _make_task("t1", "running", 30, 100))
        card.show()
        btns = card.findChildren(QPushButton)
        btn_texts = [b.text() for b in btns]
        assert "暂停" in btn_texts
        assert "取消" in btn_texts

    def test_action_uses_explicit_callback_without_parent_lookup(self, qapp):
        actions = []
        card = _TaskCard(
            "t1",
            _make_task("t1", "running"),
            action_callback=lambda task_id, action: actions.append((task_id, action)),
        )

        card._emit_action("cancel")

        assert actions == [("t1", "cancel")]

    def test_completed_card_has_clear(self, qapp):
        """已完成任务：无进度条、有清除按钮、状态标签正确。"""
        card = _TaskCard("t2", _make_task("t2", "completed", meta_type="后处理"))
        card.show()
        assert card._status_label.text() == "已完成"
        assert not card._progress_bar.isVisible()
        btns = card.findChildren(QPushButton)
        assert any(b.text() == "清除" for b in btns)

    def test_paused_card_has_resume_and_cancel(self, qapp):
        """已暂停任务有恢复和取消按钮。"""
        card = _TaskCard("t3", _make_task("t3", "paused", 50, 100))
        card.show()
        assert card._status_label.text() == "已暂停"
        btns = card.findChildren(QPushButton)
        btn_texts = [b.text() for b in btns]
        assert "恢复" in btn_texts
        assert "取消" in btn_texts

    def test_progress_bar_hidden_for_non_active(self, qapp):
        """非活跃状态不显示进度条。"""
        for status in ("completed", "failed", "cancelled"):
            card = _TaskCard("tx", _make_task("tx", status, 50, 100))
            card.show()
            assert not card._progress_bar.isVisible(), f"status={status}"

    def test_progress_bar_visible_for_active(self, qapp):
        """运行中和暂停状态显示进度条。"""
        for status in ("running", "paused"):
            card = _TaskCard("tx", _make_task("tx", status, 50, 100))
            card.show()
            assert card._progress_bar.isVisible(), f"status={status}"

    def test_metadata_name_fallback(self, qapp):
        """metadata.type 缺失时回退到 metadata.name。"""
        task = {
            "task_id": "tx",
            "status": "running",
            "progress": {},
            "metadata": {"name": "后处理流水线"},
            "created_at": time.time(),
        }
        card = _TaskCard("tx", task)
        card.show()
        assert card._name_label.text() == "后处理流水线"

    def test_default_name_when_no_metadata(self, qapp):
        """metadata 完全缺失时用默认名称。"""
        task = {
            "task_id": "tx",
            "status": "running",
            "progress": {},
            "metadata": {},
            "created_at": time.time(),
        }
        card = _TaskCard("tx", task)
        card.show()
        assert card._name_label.text() == "后台任务"

    def test_failed_card_status(self, qapp):
        """失败任务显示'失败'状态。"""
        card = _TaskCard("tx", _make_task("tx", "failed"))
        card.show()
        assert card._status_label.text() == "失败"

    def test_cancelled_card_status(self, qapp):
        """已取消任务显示'已取消'状态。"""
        card = _TaskCard("tx", _make_task("tx", "cancelled"))
        card.show()
        assert card._status_label.text() == "已取消"


class TestStatusMappings:
    """状态颜色和标签映射测试。"""

    def test_all_statuses_have_color_and_label(self):
        for status in ("running", "completed", "failed", "cancelled", "paused"):
            assert status in _STATUS_COLORS
            assert status in _STATUS_LABELS

    @pytest.mark.parametrize(
        "status,expected_label",
        [
            ("running", "运行中"),
            ("completed", "已完成"),
            ("failed", "失败"),
            ("cancelled", "已取消"),
            ("paused", "已暂停"),
        ],
    )
    def test_status_uses_domain_state_and_non_color_label(self, qapp, status, expected_label):
        assert _STATUS_COLORS[status] == status
        card = _TaskCard("state", _make_task("state", status))
        assert card.property("tbDomainState") == f"task.{status}"
        assert card._status_label.text() == expected_label
        assert expected_label in card.accessibleName()
