"""通用翻译记忆（Translation Memory / 词典）系统。

提供「一文件一 mod」+ 单表权威对象 + 双索引的翻译记忆能力，
供 FOMOD 翻译、批量翻译等场景复用过往译文。

核心概念：
- 一文件一 mod：一本词典 = 一个模组文件（.esp/.esl/.esm/txt）的词条集合，存为 .tbdict
- scope：单值属性标签（project / global），标记词典词条可使用范围
- 每本词典：单表权威对象 entries + 键索引 key_index + 文本索引 text_index
- 词条主键 sha1(mod_file_id | 原文)，不含 scope
- 多词典组合查询：同名 mod → 其余 project → 其余 global，冲突收集仲裁
"""

from src.transbridge.translation_memory.model import (
    Dictionary,
    DictionaryEntry,
    entry_id,
)
from src.transbridge.translation_memory.manager import (
    TranslationMemoryManager,
    QueryContext,
    QueryResult,
    ApplyResult,
)

__all__ = [
    "Dictionary",
    "DictionaryEntry",
    "entry_id",
    "TranslationMemoryManager",
    "QueryContext",
    "QueryResult",
    "ApplyResult",
]
