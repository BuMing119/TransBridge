"""Start a source-aware task without blocking the configuration window."""

from PyQt6.QtWidgets import QMessageBox


def start_task(window) -> None:
    from .task_launch import TaskLaunch

    if getattr(window, "_task_launch", None) is not None or window._run_controller.is_running:
        QMessageBox.warning(window, "AI 翻译", "已有任务正在启动。")
        return
    launch = TaskLaunch(window)
    window._task_launch = launch
    timer = getattr(window, "_task_refresh_timer", None)
    if timer is not None:
        timer.stop()
    refresh = getattr(window, "request_task_refresh", None)
    if refresh is not None:
        refresh()
    launch.start()
