"""词条键对齐迁移：新旧集合按 entry.key 对齐译文。

与词典套用（translation_memory）严格分离——本模块仅做键精确匹配 + 原文变化检测，
不做文本兜底。文本兜底是词典套用的职责。
"""

from transbridge.migrator.key_migrator import (
    KeyMigrationPlan,
    MigrationCandidate,
    MigrationDisposition,
    MigrationEntry,
    MigrationResult,
    migrate,
    plan_migration,
)

__all__ = [
    "KeyMigrationPlan",
    "MigrationCandidate",
    "MigrationDisposition",
    "MigrationEntry",
    "MigrationResult",
    "migrate",
    "plan_migration",
]
