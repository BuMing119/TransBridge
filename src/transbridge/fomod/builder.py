"""FOMOD 输出组装：复用 fileops/filter_rules.py 过滤侵权资源，复制保留文件。"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from src.transbridge.fileops import FilterRules, filter_files


def assemble_output(src_dir: str, dest_dir: str, rules: FilterRules | None = None) -> dict:
    """将 src_dir 组装到 dest_dir：过滤侵权资源 + 复制保留文件。

    返回 {"kept_count": int, "stripped_count": int, "dest_dir": str}。
    """
    src = Path(src_dir)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    if rules is None:
        rules = FilterRules()

    # 收集所有文件相对路径
    rel_files = [str(p.relative_to(src)) for p in src.rglob("*") if p.is_file()]
    kept, stripped = filter_files(rel_files, rules)

    for rel in kept:
        sp = src / rel
        dp = dest / rel
        dp.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(sp, dp)  # 优先硬链接（零 I/O）
        except OSError:
            shutil.copy2(sp, dp)  # 跨卷回退复制

    return {"kept_count": len(kept), "stripped_count": len(stripped), "dest_dir": str(dest)}