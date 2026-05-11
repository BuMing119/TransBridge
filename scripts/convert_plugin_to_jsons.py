
"""
从EET XML文件转换翻译到多个JSON文件的脚本

使用方法:
    python convert_plugin_to_jsons.py <eet_xml_path> <output_dir>

示例:
    python convert_plugin_to_jsons.py translations_eet.xml output_json
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.converter.translation_entry_collection_export import export_to_categorized_json_files


def main():
    # 在这里直接指定输入和输出路径
    plugin_path = Path(r"D:\MyProgram\buming1170\mods\VIGILANT SE v1801\Vigilant.esm")  # 修改为您的EET XML文件路径
    output_dir = Path(r"C:\Users\admin\Desktop\警戒者\导入测试")  # 修改为您想要输出的目录路径

    # 其他设置
    ensure_ascii = False  # 是否确保ASCII编码
    indent = 2  # JSON缩进空格数

    # 检查输入文件是否存在
    if not plugin_path.exists():
        print(f"错误: 文件不存在: {plugin_path}")
        sys.exit(1)

    print(f"开始从EET XML文件加载翻译: {plugin_path}")

    # 从EET XML文件加载翻译条目
    #collection = TranslationEntryCollection.from_eet_xml(eet_xml_path)
    collection = TranslationEntryCollection.from_plugin(plugin_path)

    print(f"加载完成，共 {len(collection)} 条翻译条目")

    # 导出到分类的JSON文件
    print(f"开始导出到JSON文件: {output_dir}")
    export_to_categorized_json_files(
        collection,
        output_dir,
        ensure_ascii=ensure_ascii,
        indent=indent
    )

    print(f"导出完成！JSON文件已保存到: {output_dir}")


if __name__ == "__main__":
    main()
