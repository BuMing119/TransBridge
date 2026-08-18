"""配置与数据目录路径工具。"""

import os
import sys


def get_data_dir() -> str:
    """获取数据目录路径（用户可写目录）。

    打包环境：使用 %APPDATA%/TransBridge/data
    开发环境：使用项目根目录下的 data
    """
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        data_dir = os.path.join(appdata, "TransBridge", "data")
    else:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        base_dir = current_dir
        while not os.path.exists(os.path.join(base_dir, "src")) and base_dir != os.path.dirname(base_dir):
            base_dir = os.path.dirname(base_dir)
        data_dir = os.path.join(base_dir, "data")

    if not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)

    return data_dir


def get_config_file_path() -> str:
    """Return the single versioned application INI path."""
    return os.path.join(get_data_dir(), "transbridge.ini")


def get_legacy_config_file_path() -> str:
    """Return the read-only V1 migration source path."""
    return os.path.join(get_data_dir(), "paratranz_config.ini")
