"""Export a plugin with EET translations to the shared categorized JSON layout."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transbridge.converter.translation_entry_collection import TranslationEntryCollection  # noqa: E402
from transbridge.converter.translation_entry_collection_export import export_to_categorized_json_files  # noqa: E402
from transbridge.parser.plugin_parser import PluginParser  # noqa: E402


def plugin_eet_to_json(
    plugin_path: str | Path,
    eet_xml_path: str | Path,
    json_path: str | Path,
    *,
    skip_empty: bool = True,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:
    """Load a plugin, apply EET translations, and write beneath ``json_path``.

    ``json_path`` keeps its existing public parameter name and denotes an output
    directory, including the ``任务/`` subdirectory for dialogue files.
    """
    print(f"正在从插件文件创建词条: {plugin_path}")
    collection = TranslationEntryCollection(
        PluginParser().parse_plugin(Path(plugin_path), skip_empty=skip_empty, raise_on_error=True)
    )
    print(f"正在使用 EET XML 更新译文: {eet_xml_path}")
    updated_count = collection.update_from_eet_xml(eet_xml_path)
    export_to_categorized_json_files(collection, json_path, ensure_ascii=ensure_ascii, indent=indent)
    print(f"已更新 {updated_count} 条译文，导出 {len(collection)} 条词条到: {json_path}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="从插件读取词条，应用 EET XML 译文，再导出分类 ParaTranz JSON。")
    parser.add_argument("plugin_path", type=Path, help="输入插件路径（.esp/.esm/.esl）")
    parser.add_argument("eet_xml_path", type=Path, help="EET XML 译文路径")
    parser.add_argument("output_dir", type=Path, help="分类 JSON 输出目录")
    parser.add_argument("--include-empty", action="store_true", help="保留原文为空的插件词条")
    parser.add_argument("--ensure-ascii", action="store_true", help="将非 ASCII 字符写为转义序列")
    parser.add_argument("--indent", type=int, default=2, help="JSON 缩进空格数（默认 2）")
    args = parser.parse_args(argv)
    plugin_eet_to_json(
        args.plugin_path,
        args.eet_xml_path,
        args.output_dir,
        skip_empty=not args.include_empty,
        indent=args.indent,
        ensure_ascii=args.ensure_ascii,
    )


if __name__ == "__main__":
    main()
