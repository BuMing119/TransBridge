
"""
将旧格式的JSON文件迁移到新格式

旧格式: id为"editid:formid"
新格式: id为"editid:formid|index"

index值从插件文件中获取，而不是自己设置。
"""

import sys
from pathlib import Path
import json

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.parser.plugin_parser import PluginParser


def migrate_old_json_to_new_format(
    old_json_path: str | Path,
    plugin_path: str | Path,
    new_json_path: str | Path,
    *,
    indent: int = 2,
    ensure_ascii: bool = False
) -> None:
    """
    将旧格式的JSON文件迁移到新格式

    Args:
        old_json_path: 旧格式JSON文件路径
        plugin_path: 插件文件路径(.esp/.esm)，用于获取index信息
        new_json_path: 新格式JSON文件输出路径
        indent: JSON缩进空格数
        ensure_ascii: 是否确保ASCII编码
    """
    # 1. 加载旧格式的JSON文件
    print(f"正在加载旧格式JSON文件: {old_json_path}")
    old_collection = TranslationEntryCollection.from_json_file(old_json_path)
    print(f"成功加载 {len(old_collection)} 个翻译条目")

    # 2. 从插件文件中获取字符串及其索引
    plugin_path = Path(plugin_path)
    if not plugin_path.exists():
        print(f"警告: 插件文件不存在: {plugin_path}")
        print("将使用默认索引值1处理所有条目")
        plugin_entries = []
    else:
        print(f"正在从插件文件获取索引信息: {plugin_path}")
        parser = PluginParser()
        plugin_entries = parser.parse_plugin(plugin_path, skip_empty=False)

    # 创建一个字典，用于快速查找字符串的索引
    # 键: (editor_id, form_id, original_text) -> 值: index
    string_to_index = {}
    for entry in plugin_entries:
        # 从id中提取editor_id和form_id
        id_parts = entry.id.split(':')
        if len(id_parts) >= 2:
            editor_id = id_parts[0]
            form_id_with_index = id_parts[1]
            form_id = form_id_with_index.split('|')[0]

            # 使用(editor_id, form_id, original)作为键
            key = (editor_id, form_id, entry.original)
            string_to_index[key] = entry.index

    print(f"成功从插件文件中获取 {len(string_to_index)} 个字符串的索引信息")

    # 3. 创建新的翻译条目集合，更新id格式
    new_collection = TranslationEntryCollection()
    updated_count = 0
    not_found_count = 0

    for old_entry in old_collection:
        # 从旧id中提取editor_id和form_id
        # 处理id可能是整数或字符串的情况
        entry_id = str(old_entry.id) if not isinstance(old_entry.id, str) else old_entry.id
        id_parts = entry_id.split(':')
        if len(id_parts) >= 2:
            editor_id = id_parts[0]
            form_id = id_parts[1]

            # 尝试从插件中获取索引
            key = (editor_id, form_id, old_entry.original)
            index = string_to_index.get(key, 1)  # 默认使用1

            if key not in string_to_index:
                not_found_count += 1
                print(f"警告: 未找到索引信息，使用默认值1 - {editor_id}:{form_id} - {old_entry.original[:50]}...")

            # 创建新id
            new_id = f"{editor_id}:{form_id}|{index}"

            # 创建新的翻译条目
            new_entry = type(old_entry)(
                id=new_id,
                key=old_entry.key,
                original=old_entry.original,
                translation=old_entry.translation,
                stage=old_entry.stage,
                context=old_entry.context
            )

            new_collection.add(new_entry)
            updated_count += 1
        else:
            # 如果id格式不符合预期，保持原样
            new_collection.add(old_entry)

    print(f"成功更新 {updated_count} 个翻译条目的id格式")
    if not_found_count > 0:
        print(f"警告: {not_found_count} 个条目未找到索引信息，使用了默认值1")

    # 4. 保存新格式的JSON文件
    print(f"正在保存新格式JSON文件: {new_json_path}")
    new_collection.to_json_file(
        new_json_path,
        ensure_ascii=ensure_ascii,
        indent=indent
    )
    print(f"成功保存 {len(new_collection)} 个翻译条目到新格式JSON文件")


def batch_migrate_jsons(
    json_dir: str | Path,
    plugin_path: str | Path,
    output_dir: str | Path,
    *,
    indent: int = 2,
    ensure_ascii: bool = False
) -> None:
    """
    批量迁移目录下的所有JSON文件到新格式

    Args:
        json_dir: 包含旧格式JSON文件的目录
        plugin_path: 插件文件路径(.esp/.esm)，用于获取index信息
        output_dir: 新格式JSON文件输出目录
        indent: JSON缩进空格数
        ensure_ascii: 是否确保ASCII编码
    """
    json_dir = Path(json_dir)
    output_dir = Path(output_dir)
    plugin_path = Path(plugin_path)

    # 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)

    # 获取目录下所有JSON文件
    json_files = list(json_dir.glob("*.json"))
    if not json_files:
        print(f"警告: 在目录 {json_dir} 中未找到任何JSON文件")
        return

    print(f"找到 {len(json_files)} 个JSON文件")

    # 批量处理每个JSON文件
    success_count = 0
    fail_count = 0

    for json_file in json_files:
        try:
            # 构建输出文件路径
            output_file = output_dir / f"{json_file.stem}_new.json"

            print(f"\n处理文件: {json_file.name}")
            migrate_old_json_to_new_format(
                old_json_path=json_file,
                plugin_path=plugin_path,
                new_json_path=output_file,
                indent=indent,
                ensure_ascii=ensure_ascii
            )
            success_count += 1
        except Exception as e:
            fail_count += 1
            print(f"错误: 处理文件 {json_file.name} 失败: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n批量处理完成: 成功 {success_count} 个, 失败 {fail_count} 个")


def main():
    """命令行入口"""
    # 默认路径
    default_json_dir = Path(r"C:\Users\admin\Desktop\警戒者\导入测试\旧json")
    default_plugin_path = Path(r"D:\MyProgram\ming1170\mods\VIGILANT SE v1801\Vigilant.esm")
    default_output_dir = Path(r"C:\Users\admin\Desktop\警戒者\导入测试\新json转换")

    # 从命令行参数获取路径
    if len(sys.argv) >= 2:
        json_dir = Path(sys.argv[1])
    else:
        json_dir = default_json_dir

    if len(sys.argv) >= 3:
        plugin_path = Path(sys.argv[2])
    else:
        plugin_path = default_plugin_path

    if len(sys.argv) >= 4:
        output_dir = Path(sys.argv[3])
    else:
        output_dir = default_output_dir

    # 执行批量转换
    try:
        batch_migrate_jsons(
            json_dir=json_dir,
            plugin_path=plugin_path,
            output_dir=output_dir,
            indent=2,
            ensure_ascii=False
        )
        print("批量迁移完成!")
        sys.exit(0)
    except Exception as e:
        print(f"批量迁移失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
