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
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
