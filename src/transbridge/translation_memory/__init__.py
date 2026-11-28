# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""翻译记忆和 FOMOD 查询接口。library 提供主库和个人库。"""

from transbridge.translation_memory.contracts import (
    TmConflictPolicy,
    TmMatchStatus,
    TranslationMemoryCandidate,
    TranslationMemoryQuery,
    TranslationMemoryQueryResult,
)
from transbridge.translation_memory.manager import (
    ApplyResult,
    QueryContext,
    QueryResult,
    TranslationMemoryManager,
)
from transbridge.translation_memory.model import (
    Dictionary,
    DictionaryEntry,
    entry_id,
)
from transbridge.translation_memory.service import TranslationMemoryQueryService

__all__ = [
    "Dictionary",
    "DictionaryEntry",
    "entry_id",
    "TranslationMemoryManager",
    "QueryContext",
    "QueryResult",
    "ApplyResult",
    "TmConflictPolicy",
    "TmMatchStatus",
    "TranslationMemoryCandidate",
    "TranslationMemoryQuery",
    "TranslationMemoryQueryResult",
    "TranslationMemoryQueryService",
]
