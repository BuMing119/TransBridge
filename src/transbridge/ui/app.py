# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
from datetime import datetime
import logging
from pathlib import Path
import sys
import traceback

from PyQt6 import sip
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from transbridge import __version__
from transbridge.application.security.diagnostic_logging import DiagnosticLogFormatter
from transbridge.bootstrap import AppRuntime, bind_runtime, build_runtime
from transbridge.config.storage_access import storage_access
from transbridge.config.storage_location import StorageLocationError
from transbridge.paratranz.config_manager import ParatranzConfig

from .context import AppContext
from .foundation.runtime import GuiFoundation
from .hang_watchdog import UiHangWatchdog
from .input_guard import install_accidental_wheel_guard
from .main_window import MainWindow

_LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

_logger = logging.getLogger(__name__)
_file_log_handler: logging.FileHandler | None = None


def _application_icon_path() -> Path | None:
    """Resolve the shared window icon in source and PyInstaller layouts."""

    bundle_root = getattr(sys, "_MEIPASS", None)
    candidate = (
        Path(bundle_root) / "transbridge" / "ui" / "assets" / "transbridge.ico"
        if bundle_root is not None
        else Path(__file__).resolve().parent / "assets" / "transbridge.ico"
    )
    return candidate if candidate.is_file() else None


def _apply_application_icon(application: QApplication) -> None:
    icon_path = _application_icon_path()
    if icon_path is None:
        _logger.warning("Application icon is unavailable")
        return
    icon = QIcon(str(icon_path))
    if icon.isNull():
        _logger.warning("Application icon could not be loaded: %s", icon_path)
        return
    application.setWindowIcon(icon)


def _setup_logging() -> None:
    """Install our file sink even when a dependency has configured root logging."""
    global _file_log_handler

    log_dir = (Path(ParatranzConfig.get_data_dir()) / "log").resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    previous = _file_log_handler
    if previous is not None and previous.stream is not None and Path(previous.baseFilename).parent == log_dir:
        root.addHandler(previous)
        return

    log_file = log_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(DiagnosticLogFormatter(_LOG_FORMAT))
    root.addHandler(handler)
    _file_log_handler = handler
    if previous is not None:
        root.removeHandler(previous)
        previous.close()
    _logger.info("TransBridge %s GUI logging initialized: %s", __version__, log_file)


def _global_exception_hook(exc_type, exc_value, exc_tb):
    """全局异常捕获器，将未捕获的异常记录到日志"""
    _logger.critical("未捕获的异常:\n%s", "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    # 调用默认异常处理器（保持原有行为）
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def create_workbench_context(runtime: AppRuntime, context) -> AppContext:
    use_cases = runtime.use_cases
    available = set(use_cases.names())

    def optional(name):
        return use_cases.resolve(name) if name in available else None

    return AppContext(
        project_projection=use_cases.resolve("project_projection"),
        project_commands=use_cases.resolve("gui_project_commands"),
        project_source_references=use_cases.resolve("project_source_references"),
        project_text_encodings=use_cases.resolve("project_text_encodings"),
        text_encoding_defaults=use_cases.resolve("text_encoding_defaults"),
        dictionary_preferences=optional("dictionary_preferences"),
        dictionary_base_dir=runtime.settings.get("translation_memory_root"),
        project_catalog=use_cases.resolve("project_catalog"),
        project_remote_bindings=use_cases.resolve("project_remote_bindings"),
        runtime_context=context,
        effective_terminology_factory=optional("effective_terminology_factory"),
        terminology_profile_service_factory=optional("terminology_profile_service_factory"),
        terminology_sync_service_factory=optional("terminology_sync_service_factory"),
        task_result_preferences_provider=use_cases.resolve("task_result_preferences").load,
        task_store=use_cases.resolve("task_store"),
    )


def main(
    runtime: AppRuntime | None = None,
    *,
    initial_project_path: str | None = None,
    initial_import_path: str | None = None,
) -> int:
    """Run the GUI adapter against one explicitly owned application runtime."""

    app = QApplication.instance() or QApplication(sys.argv)
    try:
        with storage_access():
            code, destination = _run_window(app, runtime, initial_project_path, initial_import_path)
    except StorageLocationError as exc:
        QMessageBox.critical(None, "无法打开数据文件夹", str(exc))
        return 1
    if destination is not None:
        from .settings.storage_restart import migrate_and_restart

        return migrate_and_restart(destination)
    return code


def _run_window(app, runtime, initial_project_path, initial_import_path):
    global _file_log_handler
    _setup_logging()
    sys.excepthook = _global_exception_hook
    app_runtime = runtime or build_runtime({"entrypoint": "gui"})
    binding = bind_runtime(
        app_runtime,
        "gui:main-window",
        permissions=frozenset({"gui"}),
        metadata=(("entrypoint", "gui"),),
    )
    ui_foundation: GuiFoundation | None = None
    hang_watchdog: UiHangWatchdog | None = None
    window: MainWindow | None = None
    try:
        app.setApplicationName("TransBridge")
        app.setOrganizationName("TransBridge")
        _apply_application_icon(app)
        app._transbridge_runtime = app_runtime  # type: ignore[attr-defined]
        app._transbridge_wheel_guard = install_accidental_wheel_guard(app)  # type: ignore[attr-defined]
        try:
            ui_foundation = GuiFoundation.create(app, app_runtime.use_cases.resolve("ui_preferences"))
        except Exception:  # noqa: BLE001 - appearance failure must not block the business GUI
            _logger.exception("UI Foundation startup failed; continuing with the Qt default appearance")
        app._transbridge_ui_foundation = ui_foundation  # type: ignore[attr-defined]

        # Legacy registry setup remains an entrypoint adapter until S04 migrates it.
        from transbridge.smart_assistant.agents import AgentRegistry
        from transbridge.smart_assistant.tools import register_all as register_all_tools
        from transbridge.smart_assistant.tools.task_manager import TaskManager

        TaskManager().bind_runtime(app_runtime.tasks, projection=app_runtime.task_projection)
        AgentRegistry.init_presets()
        register_all_tools()

        projection = create_workbench_context(app_runtime, binding.context)
        window = MainWindow(
            app_context=projection,
            runtime=app_runtime,
            runtime_context=binding.context,
            ui_foundation=ui_foundation,
            initial_project_path=initial_project_path,
            initial_import_path=initial_import_path,
        )
        window.show()
        hang_watchdog = UiHangWatchdog(Path(ParatranzConfig.get_data_dir()) / "log" / "ui-hangs", app)
        app.aboutToQuit.connect(hang_watchdog.close)
        hang_watchdog.start()
        code = int(app.exec())
        destination = (
            getattr(window, "storage_migration_destination", None) if getattr(window, "close_ready", False) else None
        )
    finally:
        if hang_watchdog is not None:
            hang_watchdog.close()
        # Destroy the owned widget tree on the GUI thread while its services are still alive.
        if window is not None and not sip.isdeleted(window):
            sip.delete(window)
        if ui_foundation is not None:
            ui_foundation.close()
        closed = app_runtime.close()
        if _file_log_handler is not None:
            logging.getLogger().removeHandler(_file_log_handler)
            _file_log_handler.close()
            _file_log_handler = None
    if destination is not None and not closed.is_success:
        QMessageBox.warning(None, "未迁移数据", "后台任务未能正常关闭。数据仍在原位置，请重新打开软件后再试。")
        return 1, None
    return code, destination


if __name__ == "__main__":
    raise SystemExit(main())
