# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""内存翻译记忆数据模型：DictionaryEntry 与 Dictionary。

结构：单表权威对象 + 双索引（键索引 + 文本索引）。

- DictionaryEntry：权威对象（译文 + 元数据），译文只存一份
- Dictionary：一本词典 = 一个 mod 文件（scope 单值标签 + entries 权威表 + key_index/text_index 双索引）
- 命中计数 hits 落在索引值上，键/文本两条路径独立计数，权威对象不含 hit_count
- 词条主键由 `sha1(mod_file_id | 原文)` 派生，不含 scope（身份与词典位置解耦）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha1
from typing import Any

from transbridge.application.io.identity import Provenance
from transbridge.application.io.stage_policy import Stage

# scope 合法值（单值二选一）
SCOPE_PROJECT = "project"
SCOPE_GLOBAL = "global"
VALID_SCOPES = {SCOPE_PROJECT, SCOPE_GLOBAL}

# scope 优先级（用于多词典命中候选仲裁，project 优先于 global）
SCOPE_RANK = {SCOPE_PROJECT: 2, SCOPE_GLOBAL: 1}


def entry_id(mod_file_id: str, original: str) -> str:
    """词条主键 = sha1(mod_file_id | 原文)，不含 scope。

    - mod_file_id：来源 mod 名（词典定位键）
    - original：原始原文（未规范化，保证主键稳定）
    - 同一 (mod_file_id, 原文) 在 project/global 间切换 scope 时主键不变
    """
    return sha1(f"{mod_file_id}|{original}".encode()).hexdigest()


@dataclass
class DictionaryEntry:
    """权威对象：一条译文及其元数据。

    译文只在此处存一份；键索引与文本索引通过 entry_id 指向它。
    """

    translation: str = ""
    original: str = ""  # 规范化前的原始英文（供原文变化检测 EXACT/STALE）
    source_mod: str = ""  # 来源 mod 名
    form_id_with_plugin: str = ""  # 完整 FormID|插件名（如 0001A2B3|MyMod.esp）
    imported_at: str = ""  # 首次入典时间（冲突仲裁基准，永不因命中/覆盖而变）
    updated_at: str = ""  # 内容最后变更时间
    tags: list[str] = field(default_factory=list)  # 词典标签（仅管理/筛选，不参与匹配）
    source_locale: str = ""
    target_locale: str = ""
    stage: int = Stage.UNTRANSLATED.value
    provenance: tuple[Provenance, ...] = ()
    dictionary_id: str = ""
    dictionary_revision: int = 0
    source_namespace: str = ""
    source_fingerprint: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if Stage.from_value(self.stage) is None:
            raise ValueError("dictionary entry stage is invalid")
        if (
            isinstance(self.dictionary_revision, bool)
            or not isinstance(self.dictionary_revision, int)
            or self.dictionary_revision < 0
        ):
            raise ValueError("dictionary revision must be a non-negative integer")
        if not isinstance(self.enabled, bool):
            raise TypeError("dictionary entry enabled must be a bool")
        self.provenance = tuple(self.provenance)


@dataclass
class Dictionary:
    """内存词典：scope 单值标签 + 单表权威对象 + 双索引。"""

    mod_file_id: str = ""  # 来源 mod 名
    scope: str = SCOPE_GLOBAL  # 单值标签 project|global（不参与定位/主键）
    # 权威对象表：entry_id -> DictionaryEntry（译文唯一来源）
    entries: dict[str, DictionaryEntry] = field(default_factory=dict)
    # 键索引：complete_key -> {"entry_id": str, "hits": int}
    key_index: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 文本索引：normalized_original -> {"entry_id": str, "hits": int}
    text_index: dict[str, dict[str, Any]] = field(default_factory=dict)
    dictionary_id: str = ""
    revision: int = 0

    def __post_init__(self) -> None:
        if self.scope not in VALID_SCOPES:
            raise ValueError(f"非法 scope: {self.scope!r}（合法值: {sorted(VALID_SCOPES)}）")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("dictionary revision must be a non-negative integer")
        if not self.dictionary_id:
            self.dictionary_id = self.mod_file_id
