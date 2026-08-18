"""翻译记忆数据模型：DictionaryEntry 与 Dictionary。

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

# scope 合法值（单值二选一，见 ADR-014 更新节 2026-08-14）
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
    source_mod: str = ""  # 来源 mod 名（取代旧 source 字段）
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "translation": self.translation,
            "original": self.original,
            "source_mod": self.source_mod,
            "form_id_with_plugin": self.form_id_with_plugin,
            "imported_at": self.imported_at,
            "updated_at": self.updated_at,
            "tags": list(self.tags),
            "source_locale": self.source_locale,
            "target_locale": self.target_locale,
            "stage": self.stage,
            "provenance": [item.to_dict() for item in self.provenance],
            "dictionary_id": self.dictionary_id,
            "dictionary_revision": self.dictionary_revision,
            "source_namespace": self.source_namespace,
            "source_fingerprint": self.source_fingerprint,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DictionaryEntry:
        return cls(
            translation=data.get("translation", "") or "",
            original=data.get("original", "") or "",
            # source_mod 兼容旧 source 字段（旧数据弃置，仅防御坏文件）
            source_mod=(data.get("source_mod") or data.get("source") or ""),
            form_id_with_plugin=data.get("form_id_with_plugin", "") or "",
            imported_at=data.get("imported_at", "") or "",
            updated_at=data.get("updated_at", "") or "",
            tags=list(data.get("tags") or []),
            source_locale=data.get("source_locale", "") or "",
            target_locale=data.get("target_locale", "") or "",
            stage=data.get("stage", Stage.UNTRANSLATED.value),
            provenance=tuple(Provenance.from_dict(item) for item in data.get("provenance", ())),
            dictionary_id=data.get("dictionary_id", "") or "",
            dictionary_revision=data.get("dictionary_revision", 0),
            source_namespace=data.get("source_namespace", "") or "",
            source_fingerprint=data.get("source_fingerprint", "") or "",
            # V1 records remain available to the compatibility manager, but V2
            # query will not auto-enable records whose locale cannot be proven.
            enabled=data.get("enabled", bool(data.get("source_locale") and data.get("target_locale"))),
        )


@dataclass
class Dictionary:
    """一本词典 = 一个 mod 文件：scope 单值标签 + 单表权威对象 + 双索引。"""

    mod_file_id: str = ""  # 来源 mod 名（定位键，取代旧 scope_id）
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "mod_file_id": self.mod_file_id,
            "scope": self.scope,
            "dictionary_id": self.dictionary_id or self.mod_file_id,
            "revision": self.revision,
            "entries": {k: v.to_dict() for k, v in self.entries.items()},
            "key_index": {k: dict(v) for k, v in self.key_index.items()},
            "text_index": {k: dict(v) for k, v in self.text_index.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Dictionary:
        version = data.get("schema_version", 1)
        if isinstance(version, bool) or not isinstance(version, int) or version > 2:
            raise ValueError(f"unsupported dictionary schema version: {version!r}")
        obj = cls(
            mod_file_id=data.get("mod_file_id", "") or "",
            scope=data.get("scope", SCOPE_GLOBAL),
            dictionary_id=data.get("dictionary_id", "") or data.get("mod_file_id", "") or "",
            revision=data.get("revision", 0),
        )
        obj.entries = {k: DictionaryEntry.from_dict(v) for k, v in (data.get("entries") or {}).items()}
        obj.key_index = {k: dict(v) for k, v in (data.get("key_index") or {}).items()}
        obj.text_index = {k: dict(v) for k, v in (data.get("text_index") or {}).items()}
        return obj
