"""通用文件操作工具包：归档解包/打包、目录差异分析、资源过滤规则。

与 infra/（LLM 基础设施）语义分离——本包聚焦纯文件操作，供 FOMOD 翻译、
批量翻译等场景复用。所有能力均为纯 Python，无 PyQt 依赖。
"""

from transbridge.fileops.archive import (
    ArchiveCancelledError,
    ArchiveCapabilityError,
    ArchiveExtractionError,
    ArchiveExtractor,
    ExtractionResult,
    _find_unrar,
    extract,
    inspect_archive,
    pack,
)
from transbridge.fileops.archive_policy import (
    ArchiveBudget,
    ArchiveManifest,
    ArchiveMember,
    ArchiveMemberType,
    ArchivePolicy,
    ArchivePolicyError,
)
from transbridge.fileops.differ import (
    HASH_POLICY_VERSION,
    DiffResult,
    HashReuseEvidence,
    diff_directories,
    normalize_root,
)
from transbridge.fileops.filter_rules import (
    DEFAULT_PRESET,
    PRESETS,
    RESOURCE_FILTER_POLICY_VERSION,
    FilterAction,
    FilterDecision,
    FilterRules,
    ResourceRole,
    ResourceRoleClassifier,
    classify_files,
    filter_files,
)

__all__ = [
    "ArchiveBudget",
    "ArchiveCancelledError",
    "ArchiveCapabilityError",
    "ArchiveExtractionError",
    "ArchiveExtractor",
    "ArchiveManifest",
    "ArchiveMember",
    "ArchiveMemberType",
    "ArchivePolicy",
    "ArchivePolicyError",
    "ExtractionResult",
    "extract",
    "inspect_archive",
    "pack",
    "_find_unrar",
    "diff_directories",
    "DiffResult",
    "HASH_POLICY_VERSION",
    "HashReuseEvidence",
    "normalize_root",
    "FilterRules",
    "FilterAction",
    "FilterDecision",
    "ResourceRole",
    "ResourceRoleClassifier",
    "RESOURCE_FILTER_POLICY_VERSION",
    "classify_files",
    "filter_files",
    "PRESETS",
    "DEFAULT_PRESET",
]
