"""
通过原文(original)比对，将旧JSON文件的译文迁移到新JSON文件

适用场景：旧JSON的key/id格式有bug导致无法匹配，但原文是正确的。
仅使用 original 字段进行匹配，将 translation 迁移到新文件。
"""

import sys
from pathlib import Path
import json

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.transbridge.converter.translation_entry import TranslationEntry
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection


def load_json_file_compat(path: Path) -> TranslationEntryCollection:
    """
    兼容加载旧格式JSON文件（没有id字段，用key代替）
    """
    import json
    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, list):
        raise ValueError("无效的 JSON 格式：应该是一个条目数组")

    collection = TranslationEntryCollection()
    for entry_data in data:
        # 兼容旧格式：如果没有id，用key作为id
        if "id" not in entry_data and "key" in entry_data:
            entry_data["id"] = entry_data["key"]
        entry = TranslationEntry.from_dict(entry_data)
        collection.add(entry, overwrite=True)

    return collection


def migrate_by_original(
    old_json_path: str | Path,
    new_json_path: str | Path,
    output_path: str | Path,
    *,
    overwrite: bool = False,
    use_context_fallback: bool = True,
    indent: int = 2,
    ensure_ascii: bool = False
) -> dict:
    """
    通过原文比对将旧JSON的译文迁移到新JSON

    Args:
        old_json_path: 旧JSON文件路径（包含译文）
        new_json_path: 新JSON文件路径（正确解析后的，无译文）
        output_path: 输出JSON文件路径
        overwrite: 是否覆盖新文件中已有的译文，默认False
        use_context_fallback: 是否使用context作为二级匹配，默认True
        indent: JSON缩进空格数
        ensure_ascii: 是否确保ASCII编码

    Returns:
        统计信息字典
    """
    old_json_path = Path(old_json_path)
    new_json_path = Path(new_json_path)
    output_path = Path(output_path)

    # 1. 加载旧JSON
    print(f"加载旧JSON: {old_json_path}")
    old_collection = load_json_file_compat(old_json_path)
    print(f"  旧JSON条目数: {len(old_collection)}")

    # 2. 加载新JSON
    print(f"加载新JSON: {new_json_path}")
    new_collection = load_json_file_compat(new_json_path)
    print(f"  新JSON条目数: {len(new_collection)}")

    # 3. 构建旧JSON的原文索引
    # key: original -> list[TranslationEntry] (可能有重复原文)
    old_by_original: dict[str, list[TranslationEntry]] = {}
    for entry in old_collection:
        if entry.original:
            if entry.original not in old_by_original:
                old_by_original[entry.original] = []
            old_by_original[entry.original].append(entry)

    # key: (original, context) -> TranslationEntry (更精确匹配)
    old_by_original_context: dict[tuple[str, str], TranslationEntry] = {}
    for entry in old_collection:
        if entry.original and entry.translation:
            key = (entry.original, entry.context or "")
            if key not in old_by_original_context:
                old_by_original_context[key] = entry

    # 4. 迁移译文
    stats = {
        "total": len(new_collection),
        "matched": 0,
        "updated": 0,
        "skipped_has_translation": 0,
        "skipped_no_match": 0,
        "ambiguous": 0,
    }

    updated_entries = []

    for new_entry in new_collection:
        if not new_entry.original:
            updated_entries.append(new_entry)
            continue

        # 如果新条目已有译文且不覆盖，跳过
        if new_entry.translation and not overwrite:
            stats["skipped_has_translation"] += 1
            updated_entries.append(new_entry)
            continue

        matched_old = None
        match_type = None

        # Phase 1: 尝试精确匹配 (original, context)
        if use_context_fallback and new_entry.context:
            key = (new_entry.original, new_entry.context)
            if key in old_by_original_context:
                matched_old = old_by_original_context[key]
                match_type = "original+context"

        # Phase 2: 仅用 original 匹配
        if matched_old is None:
            candidates = old_by_original.get(new_entry.original, [])
            if len(candidates) == 1:
                matched_old = candidates[0]
                match_type = "original"
            elif len(candidates) > 1:
                # 多个候选，优先选有译文的
                with_translation = [e for e in candidates if e.translation]
                if len(with_translation) == 1:
                    matched_old = with_translation[0]
                    match_type = "original(ambiguous_resolved)"
                elif with_translation:
                    # 仍有多个，取第一个
                    matched_old = with_translation[0]
                    match_type = "original(ambiguous_first)"
                    stats["ambiguous"] += 1

        if matched_old and matched_old.translation:
            stats["matched"] += 1
            # 创建更新后的条目
            updated_entry = TranslationEntry(
                id=new_entry.id,
                key=new_entry.key,
                original=new_entry.original,
                translation=matched_old.translation,
                stage=matched_old.stage if matched_old.stage > 0 else 1,
                context=new_entry.context,
                string_id=new_entry.string_id,
            )
            updated_entries.append(updated_entry)
            stats["updated"] += 1
            print(f"  [{match_type}] {new_entry.original[:50]}... -> {matched_old.translation[:30]}...")
        else:
            stats["skipped_no_match"] += 1
            updated_entries.append(new_entry)

    # 5. 构建新集合并保存
    output_collection = TranslationEntryCollection(entries=updated_entries)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_collection.to_json_file(
        output_path,
        indent=indent,
        ensure_ascii=ensure_ascii
    )

    print(f"\n保存到: {output_path}")
    print(f"统计:")
    print(f"  总条目: {stats['total']}")
    print(f"  匹配成功: {stats['matched']}")
    print(f"  实际更新: {stats['updated']}")
    print(f"  跳过(已有译文): {stats['skipped_has_translation']}")
    print(f"  跳过(无匹配): {stats['skipped_no_match']}")
    print(f"  歧义匹配: {stats['ambiguous']}")

    return stats


def merge_json_files(
    json_dir: str | Path,
    output_path: str | Path,
    *,
    indent: int = 2,
    ensure_ascii: bool = False
) -> dict:
    """
    直接合并多个JSON文件为一个文件

    适用场景：只有分割的旧JSON文件，想直接合并成一个文件。

    Args:
        json_dir: 包含多个JSON文件的目录
        output_path: 输出JSON文件路径
        indent: JSON缩进空格数
        ensure_ascii: 是否确保ASCII编码

    Returns:
        统计信息字典
    """
    json_dir = Path(json_dir)
    output_path = Path(output_path)

    # 1. 扫描并加载所有JSON文件
    print(f"扫描目录: {json_dir}")
    json_files = list(json_dir.glob("*.json"))
    if not json_files:
        print(f"错误: 目录中未找到JSON文件")
        return {"error": "no_files"}

    print(f"找到 {len(json_files)} 个JSON文件:")
    merged_collection = TranslationEntryCollection()
    stats = {"files_loaded": 0, "total_entries": 0, "failed_files": []}

    for json_file in json_files:
        print(f"  加载: {json_file.name}")
        try:
            coll = load_json_file_compat(json_file)
            count = len(coll)
            merged_collection.merge(coll)
            stats["files_loaded"] += 1
            stats["total_entries"] += count
            print(f"    -> {count} 条")
        except Exception as e:
            stats["failed_files"].append(json_file.name)
            print(f"    警告: 加载失败 - {e}")

    # 2. 保存合并结果
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged_collection.to_json_file(output_path, indent=indent, ensure_ascii=ensure_ascii)

    print(f"\n合并完成:")
    print(f"  加载文件: {stats['files_loaded']}")
    print(f"  总条目数: {stats['total_entries']}")
    if stats['failed_files']:
        print(f"  失败文件: {stats['failed_files']}")
    print(f"  输出路径: {output_path}")

    return stats


def merge_and_migrate(
    old_json_dir: str | Path,
    new_json_path: str | Path,
    output_path: str | Path,
    *,
    overwrite: bool = False,
    use_context_fallback: bool = True,
    indent: int = 2,
    ensure_ascii: bool = False
) -> dict:
    """
    合并多个分割的旧JSON文件，然后迁移译文到新JSON

    适用场景：旧JSON是分类上传时分割的小文件（如多个对话_[任务名].json），
    需要合并后统一迁移到新JSON。

    Args:
        old_json_dir: 包含多个旧JSON文件的目录
        new_json_path: 新JSON文件路径（正确解析后的单个文件）
        output_path: 输出JSON文件路径
        overwrite: 是否覆盖新文件中已有的译文
        use_context_fallback: 是否使用context作为二级匹配
        indent: JSON缩进空格数
        ensure_ascii: 是否确保ASCII编码

    Returns:
        统计信息字典
    """
    old_json_dir = Path(old_json_dir)
    new_json_path = Path(new_json_path)
    output_path = Path(output_path)

    # 1. 加载并合并所有旧JSON文件
    print(f"扫描目录: {old_json_dir}")
    old_json_files = list(old_json_dir.glob("*.json"))
    if not old_json_files:
        print(f"错误: 目录中未找到JSON文件")
        return {"error": "no_old_files"}

    print(f"找到 {len(old_json_files)} 个旧JSON文件:")
    merged_old_collection = TranslationEntryCollection()

    for json_file in old_json_files:
        print(f"  加载: {json_file.name}")
        try:
            coll = load_json_file_compat(json_file)
            merged_old_collection.merge(coll)
        except Exception as e:
            print(f"    警告: 加载失败 - {e}")

    print(f"合并后旧条目总数: {len(merged_old_collection)}")

    # 2. 加载新JSON
    print(f"\n加载新JSON: {new_json_path}")
    new_collection = load_json_file_compat(new_json_path)
    print(f"  新JSON条目数: {len(new_collection)}")

    # 3. 构建旧JSON的原文索引
    old_by_original: dict[str, list[TranslationEntry]] = {}
    for entry in merged_old_collection:
        if entry.original:
            if entry.original not in old_by_original:
                old_by_original[entry.original] = []
            old_by_original[entry.original].append(entry)

    old_by_original_context: dict[tuple[str, str], TranslationEntry] = {}
    for entry in merged_old_collection:
        if entry.original and entry.translation:
            key = (entry.original, entry.context or "")
            if key not in old_by_original_context:
                old_by_original_context[key] = entry

    # 4. 迁移译文
    stats = {
        "total": len(new_collection),
        "matched": 0,
        "updated": 0,
        "skipped_has_translation": 0,
        "skipped_no_match": 0,
        "ambiguous": 0,
    }

    updated_entries = []

    for new_entry in new_collection:
        if not new_entry.original:
            updated_entries.append(new_entry)
            continue

        if new_entry.translation and not overwrite:
            stats["skipped_has_translation"] += 1
            updated_entries.append(new_entry)
            continue

        matched_old = None
        match_type = None

        if use_context_fallback and new_entry.context:
            key = (new_entry.original, new_entry.context)
            if key in old_by_original_context:
                matched_old = old_by_original_context[key]
                match_type = "original+context"

        if matched_old is None:
            candidates = old_by_original.get(new_entry.original, [])
            if len(candidates) == 1:
                matched_old = candidates[0]
                match_type = "original"
            elif len(candidates) > 1:
                with_translation = [e for e in candidates if e.translation]
                if len(with_translation) == 1:
                    matched_old = with_translation[0]
                    match_type = "original(ambiguous_resolved)"
                elif with_translation:
                    matched_old = with_translation[0]
                    match_type = "original(ambiguous_first)"
                    stats["ambiguous"] += 1

        if matched_old and matched_old.translation:
            stats["matched"] += 1
            updated_entry = TranslationEntry(
                id=new_entry.id,
                key=new_entry.key,
                original=new_entry.original,
                translation=matched_old.translation,
                stage=matched_old.stage if matched_old.stage > 0 else 1,
                context=new_entry.context,
                string_id=new_entry.string_id,
            )
            updated_entries.append(updated_entry)
            stats["updated"] += 1
        else:
            stats["skipped_no_match"] += 1
            updated_entries.append(new_entry)

    # 5. 保存
    output_collection = TranslationEntryCollection(entries=updated_entries)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_collection.to_json_file(output_path, indent=indent, ensure_ascii=ensure_ascii)

    print(f"\n保存到: {output_path}")
    print(f"统计:")
    print(f"  总条目: {stats['total']}")
    print(f"  匹配成功: {stats['matched']}")
    print(f"  实际更新: {stats['updated']}")
    print(f"  跳过(已有译文): {stats['skipped_has_translation']}")
    print(f"  跳过(无匹配): {stats['skipped_no_match']}")
    print(f"  歧义匹配: {stats['ambiguous']}")

    return stats


def batch_migrate_by_original(
    old_json_dir: str | Path,
    new_json_dir: str | Path,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
    indent: int = 2,
    ensure_ascii: bool = False
) -> None:
    """
    批量迁移：按文件名匹配旧JSON和新JSON

    Args:
        old_json_dir: 旧JSON文件目录
        new_json_dir: 新JSON文件目录
        output_dir: 输出目录
        overwrite: 是否覆盖已有译文
        indent: JSON缩进
        ensure_ascii: 是否确保ASCII编码
    """
    old_json_dir = Path(old_json_dir)
    new_json_dir = Path(new_json_dir)
    output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    # 获取新JSON文件列表
    new_json_files = list(new_json_dir.glob("*.json"))
    if not new_json_files:
        print(f"警告: 在 {new_json_dir} 中未找到JSON文件")
        return

    print(f"找到 {len(new_json_files)} 个新JSON文件")

    success_count = 0
    fail_count = 0

    for new_json in new_json_files:
        # 尝试匹配旧JSON（同名）
        old_json = old_json_dir / new_json.name
        if not old_json.exists():
            print(f"跳过 {new_json.name}: 未找到对应旧JSON")
            continue

        output_file = output_dir / new_json.name

        print(f"\n{'='*60}")
        print(f"处理: {new_json.name}")

        try:
            migrate_by_original(
                old_json_path=old_json,
                new_json_path=new_json,
                output_path=output_file,
                overwrite=overwrite,
                indent=indent,
                ensure_ascii=ensure_ascii
            )
            success_count += 1
        except Exception as e:
            fail_count += 1
            print(f"错误: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"批量处理完成: 成功 {success_count}, 失败 {fail_count}")


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(
        description="通过原文比对迁移译文，或直接合并JSON文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 直接合并：合并目录下所有JSON为一个文件
  python scripts/migrate_by_original.py --concat old_dir/ output.json

  # 单文件迁移
  python scripts/migrate_by_original.py old.json new.json output.json

  # 合并迁移：合并旧目录所有JSON，迁移到新JSON
  python scripts/migrate_by_original.py --merge old_dir/ new.json output.json

  # 批量迁移（按文件名一一对应）
  python scripts/migrate_by_original.py --batch old_dir/ new_dir/ output_dir/

  # 覆盖已有译文
  python scripts/migrate_by_original.py old.json new.json output.json --overwrite
        """
    )

    parser.add_argument("old", nargs="?", help="旧JSON文件或目录")
    parser.add_argument("new", nargs="?", help="新JSON文件或目录")
    parser.add_argument("output", nargs="?", help="输出文件或目录")
    parser.add_argument("--concat", action="store_true",
                        help="直接合并模式：合并目录下所有JSON为一个文件（只需指定 dir 和 output）")
    parser.add_argument("--batch", action="store_true", help="批量模式（按文件名一一对应）")
    parser.add_argument("--merge", action="store_true",
                        help="合并迁移：合并旧目录下所有JSON，迁移到单个新JSON文件")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有译文")
    parser.add_argument("--no-context-fallback", action="store_true",
                        help="禁用context二级匹配")

    args = parser.parse_args()

    if args.concat:
        # 直接合并模式：old=目录, output=输出文件
        if not args.old or not args.output:
            print("错误: --concat 模式需要指定 目录 和 输出文件")
            return
        merge_json_files(
            json_dir=args.old,
            output_path=args.output,
        )
    elif args.merge:
        if not args.old or not args.new or not args.output:
            print("错误: --merge 模式需要指定 旧目录 新文件 输出文件")
            return
        merge_and_migrate(
            old_json_dir=args.old,
            new_json_path=args.new,
            output_path=args.output,
            overwrite=args.overwrite,
            use_context_fallback=not args.no_context_fallback
        )
    elif args.batch:
        if not args.old or not args.new or not args.output:
            print("错误: --batch 模式需要指定 旧目录 新目录 输出目录")
            return
        batch_migrate_by_original(
            old_json_dir=args.old,
            new_json_dir=args.new,
            output_dir=args.output,
            overwrite=args.overwrite
        )
    else:
        if not args.old or not args.new or not args.output:
            print("错误: 需要指定 旧文件 新文件 输出文件")
            return
        migrate_by_original(
            old_json_path=args.old,
            new_json_path=args.new,
            output_path=args.output,
            overwrite=args.overwrite,
            use_context_fallback=not args.no_context_fallback
        )


if __name__ == '__main__':
    main()