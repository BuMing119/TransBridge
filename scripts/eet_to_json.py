
"""
EET XML 转 JSON 脚本
从EET的XML文件中读取数据并转换为JSON格式
"""

from pathlib import Path
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection


def eet_xml_to_json(
    xml_path: str | Path,
    json_path: str | Path,
    encoding: str | None = None,
    indent: int = 2,
    ensure_ascii: bool = False
) -> None:
    """
    将EET XML文件转换为JSON文件

    Args:
        xml_path: XML文件路径
        json_path: 输出的JSON文件路径
        encoding: XML文件编码（可选）
        indent: JSON缩进空格数
        ensure_ascii: 是否确保ASCII编码（False可保留中文等非ASCII字符）
    """
    # 使用TranslationEntryCollection.from_eet_xml解析XML文件
    collection = TranslationEntryCollection.from_eet_xml(xml_path)

    # 使用TranslationEntryCollection.to_json_file导出JSON文件
    collection.to_json_file(
        json_path,
        ensure_ascii=ensure_ascii,
        indent=indent
    )

    print(f"成功转换: {xml_path} -> {json_path}")
    print(f"共转换 {len(collection)} 条记录")


def main():
    """命令行入口"""

    eet_xml_to_json(
        xml_path=r"D:\MyProgram\buming1170\mods\VIGILANT SE v1801\Vigilant_60ADF20B.xml",
        json_path=r"D:\MyProgram\buming1170\mods\VIGILANT SE v1801\Vigilant2.json",
        encoding="utf-8",
        indent=2
    )


if __name__ == '__main__':
    main()
