from types import SimpleNamespace

from PyQt6.QtCore import pyqtSignal

from tests.ui.tools import test_ai_partial_task as partial_support, test_unified_task_progress as progress_support
from transbridge.ui.tools.ai_translator import task_run
from transbridge.ui.tools.ai_translator.task_progress_presentation import TaskProgressPresentation

make_window = progress_support.make_window
make_task = partial_support.make_task
qapp = progress_support.qapp


class _ProgressWorker(progress_support._Worker):
    project_name_progress = pyqtSignal(object)
    source_finished = pyqtSignal(str)


def _names(current=45, *, phase="translate", first="first", second="second"):
    return SimpleNamespace(
        phase=phase,
        current=current,
        total=8891,
        message="正在处理项目共享名称",
        name_count=8891,
        occurrence_count=12000,
        reused_count=0,
        sources=(
            SimpleNamespace(
                key=first, current=current, total=2158, detail=f"[ 2%] {current}/2158 | 第一轮 | 任务名（63 条）"
            ),
            SimpleNamespace(key=second, current=0, total=6733, detail="[ 0%] 0/6733 | 第一轮 | 种族与派系（16 条）"),
        ),
    )


def test_project_progress_is_one_total_and_legacy_substeps_cannot_replace_it(make_window, monkeypatch):
    monkeypatch.setattr(task_run, "AiTaskWorker", _ProgressWorker)
    window, session, _ = make_window()
    worker = progress_support._start(window, session)
    worker.project_name_progress.emit(_names())
    assert window.run.progress[:2] == (45, 8891)
    activity = window.activity.activity
    assert (activity.current, activity.total, activity.message) == (45, 8891, window.run.progress_view.summary)
    assert window.bar.maximum() == 8891
    assert "项目名称首批" in window.run.progress_view.summary
    first = window.run.progress_view.source_rows["first"]
    assert (first.current, first.total) == (45, 2158)
    assert first.detail == "第一轮 | 任务名（63 条）"
    worker.progress.emit("first", "project_names", 1, 1, "翻译结果已整理")
    assert window.run.progress[:2] == (45, 8891)
    assert window.run.progress_view.source_rows["first"] == first
    worker.project_name_progress.emit(_names(60))
    assert window.run.progress[:2] == (60, 8891)
    assert window.run.progress_view.source_rows["second"].current == 0


def test_source_progress_does_not_steal_selection_or_replace_global_denominator(make_window, monkeypatch):
    monkeypatch.setattr(task_run, "AiTaskWorker", _ProgressWorker)
    window, session, _ = make_window()
    worker = progress_support._start(window, session)
    selected = window.sources.currentItem()
    worker.project_name_progress.emit(_names(8891, phase="done"))
    worker.source_started.emit("second")
    activity = window.activity.activity
    assert (activity.current, activity.total, activity.message) == (0, 2, window.run.progress_view.summary)
    worker.progress.emit("second", "翻译", 3, 200, "[ 1%] 3/200 | 第一轮 | 物品名")
    worker.source_started.emit("first")
    worker.progress.emit("first", "proofread", 1, 7, "检查术语")
    assert window.sources.currentItem() is selected
    assert window.run.progress[:2] == (0, 2)
    assert window.run.progress_view.source_rows["second"].detail == "第一轮 | 物品名"
    worker.source_finished.emit("first")
    assert window.run.progress[:2] == (1, 2)
    assert not window.run.completion_received
    assert window.run.progress_view.source_rows["first"].phase == "等待项目收尾"
    worker.progress.emit("second", "finalize", 1, 1, "正在整理结果")
    assert window.run.progress[:2] == (1, 2)


def test_pause_keeps_title_and_cancel_ignores_project_progress(make_window, monkeypatch):
    monkeypatch.setattr(task_run, "AiTaskWorker", _ProgressWorker)
    window, session, _ = make_window()
    worker = progress_support._start(window, session)
    worker.project_name_progress.emit(_names())
    worker.pause_state_changed.emit("paused")
    worker.project_name_progress.emit(_names(60))
    assert window.status.text() == "已暂停"
    assert window.run.progress[:2] == (60, 8891)
    window.run.cancel()
    worker.project_name_progress.emit(_names(200))
    assert window.status.text() == "正在取消"
    assert window.run.progress[:2] == (60, 8891)


def test_retry_resets_project_totals_and_ignores_old_worker(make_task, monkeypatch, qapp):
    class Worker(partial_support._Worker):
        project_name_progress = pyqtSignal(object)
        source_finished = pyqtSignal(str)

    monkeypatch.setattr(task_run, "AiTaskWorker", Worker)
    window, _, _ = make_task(preview_enabled=False)
    old = window.run.worker
    old.project_name_progress.emit(_names())
    old.publish(partial_support._outcome(old))
    partial_support._wait(qapp, lambda: not window.run.busy)
    assert not window.run.progress_view.source_rows
    window.run.retry()
    partial_support._wait(qapp, lambda: window.run.worker is not None and window.run.worker is not old)
    current = window.run.worker
    assert current is not old
    assert window.run.progress[:2] == (0, 1)
    assert not window.run.progress_view.names_observed
    old.project_name_progress.emit(_names(700))
    old.source_finished.emit("first")
    assert window.run.progress[:2] == (0, 1)
    current.project_name_progress.emit(_names(10))
    assert window.run.progress[:2] == (10, 8891)
    assert window.rows["first"].text(1) == "翻译代表名称"


def test_empty_project_phase_does_not_invent_a_finished_entry():
    view = TaskProgressPresentation((SimpleNamespace(key="A"),))
    event = _names(first="A")
    event.total = event.current = event.name_count = event.occurrence_count = 0
    event.sources = ()
    view.project_names(event)
    assert view.progress[:2] == (0, 0)
    assert "1/1" not in view.progress[2]


def test_project_summary_distinguishes_reused_names_from_remaining_model_work():
    view = TaskProgressPresentation((SimpleNamespace(key="first"), SimpleNamespace(key="second")))
    event = _names()
    event.name_count = 9966
    event.reused_count = 1071
    view.project_names(event)
    assert "已写入草稿 1,071 个名称（无需模型翻译）" in view.summary
    assert "可复用" not in view.summary
    assert view.progress[:2] == (45, 8891)


def test_project_closure_displays_current_work_instead_of_completed_source_flow():
    view = TaskProgressPresentation((SimpleNamespace(key="A"), SimpleNamespace(key="B")))
    view.source_finished("A")
    view.source_finished("B")
    assert view.progress[:2] == (0, 0)
    view.source_progress("A", "term_closure_load", 0, 0, "加载本来源参考")
    assert view.progress[:2] == (0, 0)
    assert "加载项目参考" in view.summary
    view.source_progress("A", "term_closure_scan", 100, 1000, "检查差异")
    assert view.progress[:2] == (100, 1000)
    assert "检查参考差异" in view.summary
    view.source_progress("A", "term_closure", 12, 246, "综合校对")
    assert view.progress[:2] == (12, 246)
    assert "0/2 个来源" in view.summary
    view.source_progress("A", "term_closure_done", 1000, 1000, "待复核 15 条")
    assert view.progress[:2] == (1, 2)
    assert "1/2 个来源" in view.summary
    view.source_progress("B", "term_closure_load", 0, 0, "加载本来源参考")
    assert view.progress[:2] == (0, 0)
    assert "B · 加载项目参考" in view.summary
    assert view.source_rows["A"].detail == "待复核 15 条"
    view.source_progress("B", "term_closure_done", 500, 500, "无需补充校对")
    assert "2/2 个来源" in view.summary and "整理任务结果" in view.summary
    assert view.progress[:2] == (0, 0)  # Task completion is still owned by the result flow.
