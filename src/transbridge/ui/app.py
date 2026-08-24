from datetime import datetime
import logging
from pathlib import Path
import sys
import traceback

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from transbridge.bootstrap import AppRuntime, bind_runtime, build_runtime
from transbridge.paratranz.config_manager import ParatranzConfig

from .context import AppContext
from .foundation.runtime import GuiFoundation
from .input_guard import install_accidental_wheel_guard
from .main_window import MainWindow

_LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

_logger = logging.getLogger(__name__)


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
    """设置日志（使用用户数据目录）"""
    log_dir = Path(ParatranzConfig.get_data_dir()) / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    logging.basicConfig(level=logging.DEBUG, format=_LOG_FORMAT, handlers=[handler])


def _global_exception_hook(exc_type, exc_value, exc_tb):
    """全局异常捕获器，将未捕获的异常记录到日志"""
    _logger.critical("未捕获的异常:\n%s", "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    # 调用默认异常处理器（保持原有行为）
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main(runtime: AppRuntime | None = None) -> int:
    """Run the GUI adapter against one explicitly owned application runtime."""

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
    try:
        app = QApplication.instance() or QApplication(sys.argv)
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

        TaskManager().bind_runtime(app_runtime.tasks)
        AgentRegistry.init_presets()
        register_all_tools()

        projection = AppContext(
            project_projection=app_runtime.use_cases.resolve("project_projection"),
            project_commands=app_runtime.use_cases.resolve("gui_project_commands"),
            project_remote_bindings=app_runtime.use_cases.resolve("project_remote_bindings"),
            runtime_context=binding.context,
        )
        window = MainWindow(
            app_context=projection,
            runtime=app_runtime,
            runtime_context=binding.context,
            ui_foundation=ui_foundation,
        )
        window.show()
        return int(app.exec())
    finally:
        if ui_foundation is not None:
            ui_foundation.close()
        app_runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
