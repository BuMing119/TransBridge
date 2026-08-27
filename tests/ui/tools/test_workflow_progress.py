from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.infra.limited_llm_client import LimitedLLMClient
from transbridge.ui.tools.ai_translator._polish_worker import _PolishWorker
from transbridge.ui.tools.ai_translator.run_view import AiMixedProgressWindow, AiWorkflowProgressWindow
from transbridge.ui.tools.ai_translator.workflow_log_store import WorkflowLogStore
from transbridge.ui.tools.ai_translator.workflow_logging_client import WorkflowLoggingLLMClient
from transbridge.ui.tools.ai_translator.workflow_progress import (
    WorkflowProgress,
    WorkflowProgressTracker,
    stages_for_profile,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _Worker(QObject):
    progress = pyqtSignal(object)
    log = pyqtSignal(str)
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, stream_log_dir: str = "", stream_log_error: str = "") -> None:
        super().__init__()
        self.stream_log_dir = stream_log_dir
        self.stream_log_error = stream_log_error
        self.is_paused = False
        self.pause_calls = 0
        self.resume_calls = 0
        self.cancel_calls = 0

    def isRunning(self) -> bool:  # noqa: N802 - Qt compatibility
        return False

    def pause(self) -> None:
        self.is_paused = True
        self.pause_calls += 1

    def resume(self) -> None:
        self.is_paused = False
        self.resume_calls += 1

    def cancel(self) -> None:
        self.cancel_calls += 1


class _Activity:
    task_activity = object()

    def __init__(self) -> None:
        self.pause_calls = 0
        self.resume_calls = 0
        self.cancel_calls = 0

    def pause(self) -> None:
        self.pause_calls += 1

    def resume(self) -> None:
        self.resume_calls += 1

    def request_cancel(self) -> None:
        self.cancel_calls += 1


def _window(
    worker: _Worker | None = None,
    activity: _Activity | None = None,
) -> tuple[AiWorkflowProgressWindow, _Worker, _Activity]:
    worker = worker or _Worker()
    activity = activity or _Activity()
    window = AiWorkflowProgressWindow(
        worker,
        activity,
        title="AI 自定义运行",
        workflow_summary="检测 → 润色 → 汇总",
        stages=(("detect", "检测"), ("polish", "润色"), ("execute", "汇总")),
    )
    return window, worker, activity


def test_stages_for_custom_profile_only_returns_enabled_stages() -> None:
    profile = SimpleNamespace(
        enable_translation=False,
        enable_consistency_check=False,
        enable_format_validation=True,
        enable_quality_gate=False,
        enable_refinement=False,
        enable_polish=True,
        enable_arbitration=False,
    )

    assert stages_for_profile(profile, include_translation=True) == (
        ("detect", "检测"),
        ("polish", "润色"),
        ("execute", "汇总"),
    )

    disabled_profile = SimpleNamespace(
        enable_translation=False,
        enable_consistency_check=False,
        enable_format_validation=False,
        enable_quality_gate=False,
        enable_refinement=False,
        enable_polish=False,
        enable_arbitration=False,
    )
    assert stages_for_profile(disabled_profile, include_translation=True) == ()


def test_stages_for_profile_places_term_extraction_before_translation() -> None:
    profile = SimpleNamespace(
        enable_translation=True,
        enable_consistency_check=False,
        enable_format_validation=False,
        enable_quality_gate=False,
        enable_refinement=False,
        enable_polish=False,
        enable_arbitration=False,
    )

    assert stages_for_profile(
        profile,
        include_translation=True,
        include_term_extraction=True,
    ) == (
        ("terms", "术语抽取"),
        ("translate", "翻译"),
    )


def test_workflow_progress_tracker_accumulates_sequential_stages_and_finishes() -> None:
    tracker = WorkflowProgressTracker((
        ("translate", "翻译"),
        ("detect", "检测"),
        ("polish", "润色"),
        ("execute", "汇总"),
    ))

    translating = tracker.update("translate", 5, 10, "正在翻译")
    assert translating is not None
    assert translating.overall_current == 125

    polishing = tracker.update("polish", 2, 4, "正在润色", success=2, failed=1)
    assert polishing is not None
    assert polishing.overall_current == 625
    assert polishing.success == 2
    assert polishing.failed == 1

    regressive = tracker.update("polish", 1, 4, "重复进度")
    assert regressive is not None
    assert regressive.overall_current == 625
    assert tracker.update("unknown", 1, 1, "未知阶段") is None

    finished = tracker.finish(success=4, pending=1)
    assert finished.stage == "done"
    assert finished.overall_current == finished.overall_total == 1000
    assert finished.message == "全部阶段已完成"
    assert finished.success == 4
    assert finished.pending == 1


def test_workflow_window_applies_stage_total_stats_and_log(qapp: QApplication) -> None:
    window, _worker, _activity = _window()

    window.apply_progress(
        WorkflowProgress(
            stage="polish",
            stage_label="润色",
            current=3,
            total=8,
            message="正在处理第 3 条",
            overall_current=375,
            success=2,
            failed=1,
            pending=4,
            new_terms=6,
        )
    )
    qapp.processEvents()

    row = window._stage_rows["polish"]
    assert (row.progress.minimum(), row.progress.maximum(), row.progress.value()) == (0, 8, 3)
    assert row.count.text() == "3 / 8"
    assert window._progress.value() == 375
    assert window._total_progress_lbl.text() == "37%"
    assert window._status.full_text == "润色：正在处理第 3 条"
    assert window._lbl_success.full_text == "成功: 2"
    assert window._lbl_failed.full_text == "失败: 1"
    assert window._lbl_pending.full_text == "待审: 4"
    assert window._lbl_terms.full_text == "新增术语: 6"
    assert "[润色] 正在处理第 3 条" in window._log.toPlainText()

    window.apply_progress(
        WorkflowProgress(
            stage="done",
            stage_label="完成",
            current=1,
            total=1,
            message="工作流完成",
            overall_current=1000,
        )
    )
    assert window._total_progress_lbl.text() == "100%"
    assert all(row.progress.value() == row.progress.maximum() for row in window._stage_rows.values())
    assert window._status.full_text == "工作流完成"
    assert "[完成] 工作流完成" in window._log.toPlainText()

    window.close()


def test_workflow_window_uses_batch_statistics_during_term_extraction() -> None:
    worker = _Worker()
    window = AiWorkflowProgressWindow(
        worker,
        _Activity(),
        title="AI 混合运行",
        workflow_summary="术语抽取 → 翻译",
        stages=(("terms", "术语抽取"), ("translate", "翻译")),
    )

    window.apply_progress(
        WorkflowProgress(
            stage="terms",
            stage_label="术语抽取",
            current=44,
            total=436,
            message="已完成术语抽取 44/436 批，本批新增候选 5 个",
            overall_current=14,
            success=44,
            failed=0,
            pending=392,
            new_terms=83,
        )
    )

    assert window._lbl_success.full_text == "完成批次: 44"
    assert window._lbl_failed.full_text == "失败批次: 0"
    assert window._lbl_pending.full_text == "剩余批次: 392"
    assert window._lbl_terms.full_text == "候选术语: 83"
    window.close()


def test_workflow_window_pause_resume_and_stop_callbacks(qapp: QApplication) -> None:
    window, worker, activity = _window()

    window._pause.click()
    qapp.processEvents()
    assert (worker.pause_calls, activity.pause_calls) == (1, 1)
    assert window._pause.text() == "▶ 继续"
    assert "已暂停" in window._status.full_text

    window._pause.click()
    qapp.processEvents()
    assert (worker.resume_calls, activity.resume_calls) == (1, 1)
    assert window._pause.text() == "⏸ 暂停"
    assert "已继续" in window._status.full_text

    window._stop.click()
    qapp.processEvents()
    assert (worker.cancel_calls, activity.cancel_calls) == (1, 1)
    assert not window._pause.isEnabled()
    assert not window._stop.isEnabled()
    assert window._status.full_text == "正在等待安全停止点"
    assert "已请求停止" in window._log.toPlainText()

    window.close()


def test_workflow_window_treats_deleted_qt_worker_as_stopped(qapp: QApplication) -> None:
    worker = QThread()
    window = AiWorkflowProgressWindow(
        worker,
        _Activity(),
        title="AI 自定义运行",
        workflow_summary="翻译",
        stages=(("translate", "翻译"),),
    )

    worker.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()

    assert sip.isdeleted(worker)
    assert window.is_running() is False
    window.close()


def test_workflow_window_opens_persisted_llm_logs(qapp: QApplication, tmp_path: Path) -> None:
    (tmp_path / "batch_001.log").write_text("raw model response", encoding="utf-8")
    (tmp_path / "stage_polish.log").write_text("1/1 润色完成", encoding="utf-8")
    (tmp_path / "term_llm.log").write_text("完整术语请求与响应", encoding="utf-8")
    window, _worker, _activity = _window(worker=_Worker(str(tmp_path)))

    assert window._llm_log_btn.isEnabled()
    window._llm_log_btn.click()
    qapp.processEvents()

    viewer = window._log_viewer
    assert viewer is not None
    assert viewer._log_selector.count() == 3
    assert {viewer._log_selector.itemText(index).split("  —  ", 1)[0] for index in range(3)} == {
        "翻译批次 1",
        "润色",
        "术语抽取对话",
    }

    batch_index = next(index for index in range(3) if viewer._log_selector.itemText(index).startswith("翻译批次 1"))
    viewer._log_selector.setCurrentIndex(batch_index)
    assert "raw model response" in viewer._text_edit.toPlainText()

    (tmp_path / "proofread_call_001.log").write_text("校对请求与响应", encoding="utf-8")
    assert viewer._log_selector.count() == 3
    viewer._refresh_btn.click()
    assert viewer._log_selector.count() == 4

    viewer.close()
    window._llm_log_btn.click()
    qapp.processEvents()
    assert window._log_viewer is viewer
    assert viewer._log_selector.count() == 4

    viewer.close()
    window.close()


def test_workflow_window_explains_unavailable_llm_logs() -> None:
    window, _worker, _activity = _window(worker=_Worker(stream_log_error="data directory is read-only"))

    assert not window._llm_log_btn.isEnabled()
    assert "data directory is read-only" in window._llm_log_btn.toolTip()
    assert "LLM 日志不可用" in window._log.toPlainText()

    window.close()


def test_workflow_logging_client_persists_prompt_and_response(tmp_path: Path) -> None:
    class Client:
        def chat(self, messages, max_tokens=0):
            assert messages[0]["content"] == "校对这段文本"
            assert max_tokens == 500
            return "校对后的回复"

        def cancel(self) -> None:
            pass

    store = WorkflowLogStore("plugin.esp", workflow="polish", log_base=tmp_path)
    client = WorkflowLoggingLLMClient(Client(), store)

    assert client.chat([{"role": "user", "content": "校对这段文本"}], 500) == "校对后的回复"
    store.close()

    content = (Path(store.log_dir) / "llm_call_001.log").read_text(encoding="utf-8")
    assert "[REQUEST TO LLM]" in content
    assert "校对这段文本" in content
    assert "[RESPONSE FROM LLM]" in content
    assert "校对后的回复" in content


def test_workflow_logging_client_records_shared_request_budget_metrics(tmp_path: Path) -> None:
    class Client:
        def chat(self, _messages, max_tokens=0):
            return "ok"

        def cancel(self) -> None:
            pass

    store = WorkflowLogStore("plugin.esp", workflow="mixed", log_base=tmp_path)
    limited = LimitedLLMClient(Client(), AiRequestBudget(2))
    client = WorkflowLoggingLLMClient(limited, store)

    assert client.chat([{"role": "user", "content": "request"}], 20) == "ok"
    store.close()

    content = (Path(store.log_dir) / "llm_call_001.log").read_text(encoding="utf-8")
    assert "[REQUEST BUDGET]" in content
    assert "in_flight=1" in content
    assert "peak_in_flight=1" in content


def test_grouped_term_logging_keeps_complete_conversations_in_one_tab(tmp_path: Path) -> None:
    class Client:
        def chat(self, messages, max_tokens=0):
            return f"response:{messages[0]['content']}:{max_tokens}"

        def cancel(self) -> None:
            pass

    store = WorkflowLogStore("plugin.esp", workflow="mixed", log_base=tmp_path)
    client = WorkflowLoggingLLMClient(Client(), store, channel_prefix="term_llm", grouped=True)

    client.chat([{"role": "user", "content": "first prompt"}], 1000)
    client.chat([{"role": "user", "content": "second prompt"}], 1000)
    store.close()

    content = (Path(store.log_dir) / "term_llm.log").read_text(encoding="utf-8")
    assert content.count("[REQUEST TO LLM]") == 2
    assert "[CALL 001]" in content and "[CALL 002]" in content
    assert "first prompt" in content and "response:first prompt:1000" in content
    assert "second prompt" in content and "response:second prompt:1000" in content
    assert content.count("[END CALL]") == 2


def test_workflow_logging_failures_do_not_change_llm_result_or_error() -> None:
    class BrokenStore:
        def write_chunk(self, _channel: str, _content: str) -> None:
            raise OSError("disk full")

        def write_line(self, _channel: str, _content: str) -> None:
            raise OSError("disk full")

    class SuccessfulClient:
        def chat(self, _messages, _max_tokens=0):
            return "model result"

        def cancel(self) -> None:
            pass

    class FailingClient(SuccessfulClient):
        def chat(self, _messages, _max_tokens=0):
            raise RuntimeError("provider failure")

    messages = [{"role": "user", "content": "text"}]
    assert WorkflowLoggingLLMClient(SuccessfulClient(), BrokenStore()).chat(messages) == "model result"
    with pytest.raises(RuntimeError, match="provider failure"):
        WorkflowLoggingLLMClient(FailingClient(), BrokenStore()).chat(messages)


def test_workflow_log_store_ignores_late_writes_after_close(tmp_path: Path) -> None:
    store = WorkflowLogStore("plugin.esp", workflow="mixed", log_base=tmp_path)
    store.write_chunk("batch_001", "complete response")
    store.close()

    store.write_chunk("batch_001", "late chunk")

    assert (Path(store.log_dir) / "batch_001.log").read_text(encoding="utf-8") == "complete response"


def test_workflow_log_directory_failure_does_not_block_worker(monkeypatch) -> None:
    def fail_data_dir() -> str:
        raise OSError("read-only data directory")

    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator.workflow_log_store.ParatranzConfig.get_data_dir",
        fail_data_dir,
    )

    store = WorkflowLogStore("plugin.esp", workflow="polish")
    store.write_line("workflow", "still running")
    store.close()

    assert not store.is_available
    assert store.log_dir == ""
    assert store.last_error == "read-only data directory"


def test_mixed_window_consumes_workflow_progress_signal(qapp: QApplication) -> None:
    worker = _Worker()
    activity = _Activity()
    profile = SimpleNamespace(
        summary="翻译 → 润色 → 汇总",
        enable_translation=True,
        enable_consistency_check=False,
        enable_format_validation=False,
        enable_quality_gate=False,
        enable_refinement=False,
        enable_polish=True,
        enable_arbitration=False,
    )
    window = AiMixedProgressWindow(worker, activity, profile=profile)

    worker.progress.emit(
        WorkflowProgress(
            stage="translate",
            stage_label="翻译",
            current=7,
            total=10,
            message="已完成 7 条",
            overall_current=233,
            success=6,
            failed=1,
        )
    )
    qapp.processEvents()

    row = window._stage_rows["translate"]
    assert (row.progress.value(), row.progress.maximum()) == (7, 10)
    assert window._total_progress_lbl.text() == "23%"
    assert window._status.full_text == "翻译：已完成 7 条"
    assert window._lbl_success.full_text == "成功: 6"
    assert window._lbl_failed.full_text == "失败: 1"
    assert "[翻译] 已完成 7 条" in window._log.toPlainText()

    window.close()


def test_polish_worker_forwards_stage_progress_logs_and_terminal_stats(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator.workflow_log_store.ParatranzConfig.get_data_dir",
        lambda: str(tmp_path),
    )
    profile = SimpleNamespace(
        enable_translation=False,
        enable_consistency_check=True,
        enable_format_validation=False,
        enable_quality_gate=False,
        enable_refinement=False,
        enable_polish=False,
        enable_arbitration=False,
    )

    class Pipeline:
        def __init__(self) -> None:
            self.profile = profile

        def process(self, _entries, **kwargs):
            kwargs["progress_callback"]("detect", 1, 3, "已检测 1/3")
            kwargs["log_callback"]("格式检查完成")
            return {
                "pass": SimpleNamespace(accepted=True, verdict="pass", issues=()),
                "pending": SimpleNamespace(accepted=False, verdict="pending", issues=(object(),)),
                "reject": SimpleNamespace(accepted=False, verdict="reject", issues=()),
            }

    worker = _PolishWorker(lambda: Pipeline(), [object(), object(), object()], profile=profile)
    detailed = []
    logs = []
    results = []
    worker.detailed_progress.connect(detailed.append)
    worker.log.connect(logs.append)
    worker.finished_all.connect(results.append)

    worker.run()

    assert detailed[0].stage == "detect"
    assert detailed[0].current == 1
    assert detailed[-1].stage == "done"
    assert (detailed[-1].success, detailed[-1].failed, detailed[-1].pending, detailed[-1].issues) == (1, 1, 1, 1)
    assert logs == ["格式检查完成"]
    assert set(results[0]) == {"pass", "pending", "reject"}
    log_dir = Path(worker.stream_log_dir)
    assert (log_dir / "stage_detect.log").read_text(encoding="utf-8") == "1/3 已检测 1/3\n"
    assert (log_dir / "workflow.log").read_text(encoding="utf-8") == "格式检查完成\n"
