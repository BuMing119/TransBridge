#!/usr/bin/env python3
"""
将JSON文件分割成两个等份
用法: python split_json.py <input.json> [output_dir]
"""

import json
import sys
from pathlib import Path


def split_json(input_path: str, output_dir: str = None):
    """将JSON文件分割成两个等份"""
    input_file = Path(input_path)

    if not input_file.exists():
        print(f"错误: 文件不存在 - {input_file}")
        return

    if output_dir is None:
        output_dir = input_file.parent
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    print(f"读取文件: {input_file}")
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, list):
        print("错误: JSON根元素不是数组，无法分割")
        return

    total = len(data)
    mid = total // 2

    print(f"总条目数: {total}")
    print(f"每份条目数: {mid} 和 {total - mid}")

    stem = input_file.stem
    suffix = input_file.suffix

    # 第一份
    part1_path = output_dir / f"{stem}_part1{suffix}"
    with open(part1_path, 'w', encoding='utf-8') as f:
        json.dump(data[:mid], f, ensure_ascii=False, indent=2)
    print(f"已写入: {part1_path}")

    # 第二份
    part2_path = output_dir / f"{stem}_part2{suffix}"
    with open(part2_path, 'w', encoding='utf-8') as f:
        json.dump(data[mid:], f, ensure_ascii=False, indent=2)
    print(f"已写入: {part2_path}")

    print("完成!")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    input_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    split_json(input_path, output_dir)