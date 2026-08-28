"""文件解析器子包 — 多格式文件解析为结构化文档。

注册模式:
  解析器通过两种方式被发现:
  1. FileParser.__subclasses__() — 自动发现已导入的子类（依赖于导入顺序）
  2. FileParser._registry        — 显式注册表（补充方式，不依赖导入顺序）

  推荐调用 register_all() 显式注册所有内置解析器，确保 FileParser.get_parser()
  在任何导入顺序下都能正常工作。
"""

from .base import FileParser, ParsedDocument
from .binary_parser import BinaryFileParser
from .paratranz_parser import ParatranzParser
from .text_parser import TextFileParser


def register_all() -> None:
    """显式注册所有内置文件解析器（推荐 API）。

    调用 FileParser.register_parser() 将各解析器子类加入显式注册表，
    确保 FileParser.get_parser() 不受导入顺序影响。
    """
    FileParser.register_parser(TextFileParser)
    FileParser.register_parser(BinaryFileParser)
    FileParser.register_parser(ParatranzParser)


__all__ = [
    "FileParser",
    "ParsedDocument",
    "TextFileParser",
    "BinaryFileParser",
    "ParatranzParser",
    "register_all",
]
