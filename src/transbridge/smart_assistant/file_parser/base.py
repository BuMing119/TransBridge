"""文件解析器抽象基类与数据结构。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedDocument:
    """解析后的结构化文档。"""

    source_path: Path
    format: str  # "excel"/"csv"/"markdown"/"pdf"/"word"/"paratranz"/"text"/"json"
    title: str = ""
    sections: list[dict] = field(default_factory=list)
    raw_text: str = ""  # 纯文本提取（供向量嵌入）
    metadata: dict = field(default_factory=dict)

    @property
    def summary(self) -> str:
        """文档摘要（前 300 字符）。"""
        preview = self.raw_text[:300].replace("\n", " ")
        if len(self.raw_text) > 300:
            preview += "…"
        return f"[{self.format}] {self.title}: {preview}"


class FileParser(ABC):
    """文件解析器抽象基类。"""

    supported_extensions: list[str] = []

    # 显式注册表：作为 cls.__subclasses__() 的补充，避免导入顺序问题
    _registry: dict[str, type["FileParser"]] = {}

    @abstractmethod
    def parse(self, path: Path) -> ParsedDocument: ...

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in self.supported_extensions

    @classmethod
    def register_parser(cls, parser_cls: type["FileParser"]) -> None:
        """显式注册解析器子类，作为 __subclasses__() 的补充。

        在子类模块导入后调用，避免依赖 cls.__subclasses__() 的导入顺序。
        """
        cls._registry[parser_cls.__name__] = parser_cls

    @classmethod
    def get_parser(cls, path: Path) -> "FileParser | None":
        # 优先使用 __subclasses__()（自动发现已导入的子类）
        for sub in cls.__subclasses__():
            instance = sub()
            if instance.can_handle(path):
                return instance
        # 回退到显式注册表（处理导入顺序问题）
        for sub in cls._registry.values():
            instance = sub()
            if instance.can_handle(path):
                return instance
        return None
