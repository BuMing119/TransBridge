# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""List Excel workbooks visible in the desktop translation report history."""

from __future__ import annotations

from datetime import datetime
import os
import re
from stat import S_ISREG

from transbridge.config.paths import get_data_dir


def parse_report_filename(filename: str) -> dict:
    """解析报告文件名，提取 esp_stem、mode、timestamp。

    文件名格式: {esp_stem}_{mode}_report_{YYYYMMDD_HHMMSS}.xlsx
    """
    result = {"esp_stem": "?", "mode": "?", "timestamp": None}
    name = filename.replace(".xlsx", "")
    # 匹配模式: <esp>_<mode>_report_<YYYYMMDD_HHMMSS>
    m = re.match(r"(.+)_(translate|polish)_report_(\d{8}_\d{6})$", name)
    if m:
        result["esp_stem"] = m.group(1)
        mode_map = {"translate": "翻译", "polish": "润色"}
        result["mode"] = mode_map.get(m.group(2), m.group(2))
        try:
            result["timestamp"] = datetime.strptime(m.group(3), "%Y%m%d_%H%M%S")
        except ValueError:
            pass
    elif re.match(r"postprocess-report-[0-9a-f]{16}$", name):
        result["mode"] = "AI 翻译"
    elif re.match(r"polish-report-[0-9a-f]{16}$", name):
        result["mode"] = "润色"
    elif re.match(r"mixed-report-[0-9a-f]{16}$", name):
        result["mode"] = "混合"
    return result


def list_exported_reports() -> list[dict]:
    """扫描 data/ai_translator/*/reports/ 下所有 .xlsx 文件。"""
    reports = []
    base_dir = os.path.join(get_data_dir(), "ai_translator")
    if not os.path.isdir(base_dir):
        return reports

    for esp_dir in os.listdir(base_dir):
        reports_dir = os.path.join(base_dir, esp_dir, "reports")
        if not os.path.isdir(reports_dir):
            continue
        for fname in os.listdir(reports_dir):
            if not fname.endswith(".xlsx"):
                continue
            full_path = os.path.join(reports_dir, fname)
            info = parse_report_filename(fname)
            try:
                stat = os.stat(full_path)
                if not S_ISREG(stat.st_mode):
                    continue
                size = stat.st_size
                modified_at = datetime.fromtimestamp(stat.st_mtime)
            except FileNotFoundError:
                continue
            except OSError:
                size = 0
                modified_at = None
            reports.append({
                "path": full_path,
                "filename": fname,
                "esp_stem": esp_dir if info["esp_stem"] == "?" else info["esp_stem"],
                "mode": info["mode"],
                "timestamp": info["timestamp"] or modified_at,
                "size": size,
            })

    # 按时间降序
    reports.sort(key=lambda r: r["timestamp"] or datetime.min, reverse=True)
    return reports
