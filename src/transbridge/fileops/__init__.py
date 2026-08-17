"""通用文件操作工具包：归档解包/打包、目录差异分析、资源过滤规则。

与 infra/（LLM 基础设施）语义分离——本包聚焦纯文件操作，供 FOMOD 翻译、
批量翻译等场景复用。所有能力均为纯 Python，无 PyQt 依赖。
"""

from src.transbridge.fileops.archive import extract, pack, _find_unrar
from src.transbridge.fileops.differ import diff_directories, DiffResult, normalize_root
from src.transbridge.fileops.filter_rules import FilterRules, filter_files, PRESETS, DEFAULT_PRESET

__all__ = [
    "extract", "pack", "_find_unrar",
    "diff_directories", "DiffResult", "normalize_root",
    "FilterRules", "filter_files", "PRESETS", "DEFAULT_PRESET",
]