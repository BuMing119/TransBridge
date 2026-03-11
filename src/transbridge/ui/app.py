import sys
import logging
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from .main_window import MainWindow

_LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"


def _get_base_dir() -> Path:
    """获取程序根目录（兼容开发环境和 PyInstaller 打包环境）"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # 开发环境：向上查找含 src 的目录
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "src").exists():
            return parent
    return current.parent


def _setup_logging() -> None:
    log_dir = _get_base_dir() / "data" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    logging.basicConfig(level=logging.DEBUG, format=_LOG_FORMAT, handlers=[handler])


def main():
    _setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("TransBridge")
    app.setOrganizationName("TransBridge")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
