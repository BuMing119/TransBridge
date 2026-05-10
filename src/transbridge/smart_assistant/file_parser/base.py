"""文件解析器抽象基类与数据结构。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedDocument:
    """解析后的结构化文档。"""
    source_path: Path
    format: str           # "excel"/"csv"/"markdown"/"pdf"/"word"/"paratranz"/"text"/"json"
    title: str = ""
    sections: list[dict] = field(default_factory=list)
    raw_text: str = ""    # 纯文本提取（供向量嵌入）
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

    @abstractmethod
    def parse(self, path: Path) -> ParsedDocument: ...

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in self.supported_extensions

    @classmethod
    def get_parser(cls, path: Path) -> "FileParser | None":
        for sub in cls.__subclasses__():
            instance = sub()
            if instance.can_handle(path):
                return instance
        return None
