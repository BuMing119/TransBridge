"""Regression coverage for useful process events, separate from LLM diagnostics."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tests.ui.tools import test_unified_task_progress as support
from transbridge.ai_translator.activity import ActivityKind, ActivityMessage, batch_message
from transbridge.ui.tools.ai_translator.task_activity import ActivityBatch, concise_event
from transbridge.ui.tools.ai_translator.task_activity_panel import TaskActivityPanel
from transbridge.ui.tools.ai_translator.task_feedback import TaskFeedback

qapp = support.qapp


@pytest.mark.parametrize(
    ("message", "kind", "expected"),
    [
        ("[3] ❌ API 调用失败（12 条）: timeout https://private/key", ActivityKind.ERROR, "12 条模型请求失败"),
        ("⚠ QualityGate 批次异常: secret", ActivityKind.WARNING, "本批校对未完成"),
        ("[3] ↩ 6/12 条未获译文，对半拆分重试", ActivityKind.RETRY, "6/12 条未获译文"),
        ("[3] ❌ 流式暂存结果未能持久化，将在后续运行中重试", ActivityKind.ERROR, "请检查磁盘"),
        ("恢复记录保存失败：D:/private/data；请勿依赖退出恢复。", ActivityKind.ERROR, "暂时不要退出"),
    ],
)
def test_failures_and_fractional_retries_survive_without_exposing_diagnostics(message, kind, expected):
    event = concise_event(message)
    assert event is not None and event.kind == kind
    assert expected in event and "private" not in event and "secret" not in event
    if message.startswith("[3]"):
        assert event.batch == 3


@pytest.mark.parametrize(
    "message",
    ["[3] 已完成：", "[3] 开始翻译：", "术语匹配时长:0.02 s", "任务3：name", "翻译词条数：12", "Name -> 译文"],
)
def test_per_batch_noise_does_not_enter_activity(message):
    assert concise_event(message) is None


def test_kind_time_and_batch_survive_repeated_boundary_normalization():
    event = ActivityMessage("内容过长，缩小后继续", ActivityKind.RETRY, occurred_at=datetime(2026, 10, 26, tzinfo=UTC))
    event = batch_message(7, event)
    normalized = concise_event(concise_event(event))
    assert (normalized.kind, normalized.batch, normalized.occurred_at) == (event.kind, 7, event.occurred_at)
    assert isinstance(normalized, str)
    assert concise_event(batch_message(1, ActivityMessage("匹配耗时", ActivityKind.DETAIL))) is None


def test_compact_activity_groups_repeats_and_keeps_source_and_batch(qapp):
    panel = TaskActivityPanel()
    panel.show()
    try:
        event = ActivityMessage("请求失败，结束后可重试", ActivityKind.ERROR, batch=2)
        panel.append(ActivityBatch((("A.esp", event), ("A.esp", event), ("B.esp", event))))
        lines = panel.logs.toPlainText().splitlines()
        assert len(lines) == 2
        assert "A.esp · 第 3 批" in lines[0] and "连续 2 次" in lines[0]
        assert "B.esp · 第 3 批" in lines[1] and "连续" not in lines[1]
        assert not panel.logs.isVisible() and panel.notice.isVisible()
        panel.append("已进入下一阶段")
        assert "B.esp" in panel.notice.text()
        panel.toggle.click()
        assert panel.logs.isVisible() and not panel.recent.isVisible()
    finally:
        panel.close()


def test_buffer_pressure_preserves_warning_before_completion(qapp):
    batches = []
    feedback = TaskFeedback(
        progress=lambda *args: None, project_progress=lambda _: None, log_batch=batches.append, max_logs=3
    )
    feedback.log("A", ActivityMessage("本批未完成，请检查结果", ActivityKind.WARNING, batch=8))
    for index in range(20):
        feedback.log("B", f"阶段 {index} 完成")
    feedback.finish(lambda: None)
    events = [(source, event) for batch in batches for source, event in batch]
    assert any(source == "A" and event.kind == ActivityKind.WARNING and event.batch == 8 for source, event in events)
    panel = TaskActivityPanel()
    try:
        panel.append(ActivityBatch((source, concise_event(event)) for source, event in events))
        assert "A · 第 9 批" in panel.notice.text()
        panel.append(ActivityBatch(("B", ActivityMessage(f"阶段 {index} 完成")) for index in range(600)))
        assert panel.logs.document().blockCount() == 500
        assert "第 9 批" in panel.notice.text()
    finally:
        panel.close()


def test_elapsed_and_unchanged_progress_do_not_invent_a_stall_or_eta(qapp):
    panel = TaskActivityPanel()
    run = SimpleNamespace(progress=(0, 10, "翻译"), rows={}, status="运行中", running=True, completion_received=False)
    panel._clock = lambda: 100.0
    panel._started = 100.0
    try:
        panel.observe(run)
        panel._clock = lambda: 140.0
        panel.observe(run)
        assert "40 秒" in panel.timing.text() and "请求状态" in panel.timing.text()
        run.progress = (1, 10, "翻译")
        panel.observe(run)
        assert "距上次进度更新 0 秒" in panel.timing.text()
        run.running = False
        panel.observe(run)
        assert not panel.timer.isActive()
        assert "距上次进度更新" not in panel.timing.text()
    finally:
        panel.timer.stop()
        panel.close()
