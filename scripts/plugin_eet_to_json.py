
"""
插件+EET转JSON脚本
1. 从插件文件创建翻译项
2. 通过EET XML更新翻译
3. 导出为JSON文件
"""

from pathlib import Path
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.converter.translation_entry_collection_export import export_to_categorized_json_files


def plugin_eet_to_json(
    plugin_path: str | Path,
    eet_xml_path: str | Path,
    json_path: str | Path,
    *,
    skip_empty: bool = True,
    indent: int = 2,
    ensure_ascii: bool = False
) -> None:
    """
    从插件文件创建翻译项，通过EET更新翻译，最后导出为JSON

    Args:
        plugin_path: 插件文件路径(.esp/.esm)
        eet_xml_path: EET XML文件路径
        json_path: 输出的JSON文件路径
        skip_empty: 是否跳过空字符串(默认为True)
        indent: JSON缩进空格数
        ensure_ascii: 是否确保ASCII编码（False可保留中文等非ASCII字符）
    """
    # 步骤1: 从插件文件创建翻译项集合
    print(f"正在从插件文件创建翻译项: {plugin_path}")
    collection = TranslationEntryCollection.from_plugin(
        plugin_path,
        skip_empty=skip_empty,
        overwrite=True
    )
    print(f"成功创建 {len(collection)} 个翻译项")

    # 步骤2: 使用EET XML更新翻译
    print(f"正在使用EET XML更新翻译: {eet_xml_path}")
    updated_count = collection.update_from_eet_xml(eet_xml_path)
    print(f"成功更新 {updated_count} 个翻译项")

    # 步骤3: 导出为JSON文件
    # print(f"正在导出到JSON文件: {json_path}")
    # collection.to_json_file(
    #     json_path,
    #     ensure_ascii=ensure_ascii,
    #     indent=indent
    # )

    export_to_categorized_json_files(
        collection,
        json_path,
        ensure_ascii=ensure_ascii,
        indent=indent
    )
    print(f"成功导出 {len(collection)} 个翻译项到JSON文件")


def main():
    """命令行入口"""
    # 默认路径
    default_plugin_path = Path(r"D:\MyProgram\buming1170\mods\Interesting NPCs SE (3DNPC)\3DNPC.esp")
    default_eet_xml_path = Path(r"C:\Users\admin\Desktop\3DNPC\3DNPC_B675CB1D.xml")
    default_json_path = Path(r"C:\Users\admin\Desktop\3DNPC\划分")

    # 使用默认路径执行转换
    plugin_eet_to_json(
        plugin_path=default_plugin_path,
        eet_xml_path=default_eet_xml_path,
        json_path=default_json_path,
        skip_empty=True,
        indent=2,
        ensure_ascii=False
    )


if __name__ == '__main__':
    main()
