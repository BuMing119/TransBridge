import sys
import logging
import traceback
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from .main_window import MainWindow
from ..paratranz.config_manager import ParatranzConfig

_LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

_logger = logging.getLogger(__name__)


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
    _logger.critical(
        "未捕获的异常:\n%s",
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    )
    # 调用默认异常处理器（保持原有行为）
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main():
    _setup_logging()
    sys.excepthook = _global_exception_hook
    app = QApplication(sys.argv)
    app.setApplicationName("TransBridge")
    app.setOrganizationName("TransBridge")
    # 初始化 Phase 2 Agent 系统
    from src.transbridge.smart_assistant.agents import AgentRegistry
    AgentRegistry.init_presets()
    # 显式注册所有内置工具（替代模块级导入副作用，消除循环导入风险）
    from src.transbridge.smart_assistant.tools import register_all as register_all_tools
    register_all_tools()
    # 条件启动 MCP Server
    from src.transbridge.paratranz.config_manager import LLMConfig
    llm_cfg = LLMConfig.load_from_file()
    if llm_cfg.mcp_enabled:
        from src.transbridge.smart_assistant.mcp import MCPAdapter, MCPServer
        import threading
        mcp_config = {
            "admin_tool_whitelist": llm_cfg.mcp_admin_tool_whitelist,
            "write_tool_policy": llm_cfg.mcp_write_tool_policy,
        }
        adapter = MCPAdapter(ToolRegistry, mcp_config)
        server = MCPServer(ToolRegistry, adapter)
        t = threading.Thread(target=server.run_stdio, daemon=True)
        t.start()
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
