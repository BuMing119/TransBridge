"""持久化工具：原子写入 + 名称校验。"""

import json
from pathlib import Path


def atomic_write_json(path: Path, data: dict) -> None:
    """原子写入 JSON：写 .tmp → os.replace，防写入中断损坏文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def validate_name(name: str, *, allow_spaces: bool = True) -> str:
    """校验项目/版本/快照名，拒绝路径遍历字符。

    返回 stripped name；非法字符抛出 ValueError。
    """
    name = name.strip()
    if not name:
        raise ValueError("名称不能为空")
    if ".." in name:
        raise ValueError("名称不能包含 '..'")
    if "/" in name or "\\" in name:
        raise ValueError("名称不能包含路径分隔符")
    if not allow_spaces and " " in name:
        raise ValueError("名称不能包含空格")
    return name
