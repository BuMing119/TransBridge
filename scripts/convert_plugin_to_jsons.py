"""Convert a plugin to categorized JSON: uv run python scripts/convert_plugin_to_jsons.py plugin.esp output_dir."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

# Keep direct script execution usable from a source checkout as well as an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transbridge.converter.translation_entry_collection import TranslationEntryCollection  # noqa: E402
from transbridge.converter.translation_entry_collection_export import export_to_categorized_json_files  # noqa: E402
from transbridge.parser.plugin_parser import PluginParser  # noqa: E402


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="从插件导出分类 ParaTranz JSON，任务文件优先使用任务原名。")
    parser.add_argument("plugin_path", type=Path, help="输入插件路径（.esp/.esm/.esl）")
    parser.add_argument("output_dir", type=Path, help="分类 JSON 输出目录")
    parser.add_argument("--ensure-ascii", action="store_true", help="将非 ASCII 字符写为转义序列")
    parser.add_argument("--indent", type=int, default=2, help="JSON 缩进空格数（默认 2）")
    args = parser.parse_args(argv)
    print(f"正在从插件加载词条: {args.plugin_path}")
    collection = TranslationEntryCollection(PluginParser().parse_plugin(args.plugin_path, raise_on_error=True))
    export_to_categorized_json_files(collection, args.output_dir, ensure_ascii=args.ensure_ascii, indent=args.indent)
    print(f"已导出 {len(collection)} 条词条到: {args.output_dir}")


if __name__ == "__main__":
    main()
