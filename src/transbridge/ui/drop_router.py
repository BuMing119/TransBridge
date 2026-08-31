"""Read-only classification of dropped files into canonical shell intents.

The router is deliberately a proposal boundary: it may inspect bounded local
metadata, but it never parses into a collection, opens a project, extracts an
archive, touches a repository, or dispatches an intent.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import os
from pathlib import Path
import stat
from types import MappingProxyType
import zipfile

from transbridge.application.io import FormatCatalog, FormatId, ProbeRequest, ProbeStatus, SourceDescriptor
from transbridge.fileops.archive import (
    ArchiveCapabilityError,
    ArchiveExtractionError,
    inspect_archive,
)
from transbridge.fileops.archive_policy import (
    ArchiveBudget,
    ArchiveManifest,
    ArchiveMember,
    ArchiveMemberType,
    ArchivePolicy,
    ArchivePolicyError,
)
from transbridge.ui.shell.action_catalog import IntentId


class DropKind(StrEnum):
    PLUGIN = "plugin"
    PROJECT_ARCHIVE = "project-archive"
    JSON = "json"
    EET = "eet"
    XT = "xt"
    SST = "sst"
    STRINGS = "strings"
    STRINGS_DIRECTORY = "strings-directory"
    FOMOD_ARCHIVE = "fomod-archive"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"


class DropResolutionStatus(StrEnum):
    CANDIDATE = "candidate"
    NEEDS_CHOICE = "needs-choice"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class DropBudget:
    max_inputs: int = 16
    max_probe_bytes: int = 8 * 1024 * 1024
    max_directory_entries: int = 4096
    max_directory_bytes: int = 2 * 1024 * 1024 * 1024
    archive: ArchiveBudget = field(
        default_factory=lambda: ArchiveBudget(
            max_entries=50_000,
            max_total_uncompressed=8 * 1024 * 1024 * 1024,
            max_single_file=4 * 1024 * 1024 * 1024,
            max_compression_ratio=1000.0,
            max_depth=32,
            timeout_seconds=30.0,
        )
    )

    def __post_init__(self) -> None:
        for name in ("max_inputs", "max_probe_bytes", "max_directory_entries", "max_directory_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class DropDiagnostic:
    code: str
    message: str
    recovery: str
    path: str | None = None

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip() or not self.recovery.strip():
            raise ValueError("drop diagnostics require code, message and recovery")


@dataclass(frozen=True, slots=True)
class DropItem:
    path: str
    kind: DropKind
    format_ids: tuple[FormatId, ...] = ()
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DropCandidatePlan:
    """One inert hand-off to the existing intent owner."""

    intent_id: IntentId
    payload: tuple[tuple[str, str], ...]
    summary: str
    requires_confirmation: bool = True

    def __post_init__(self) -> None:
        keys = tuple(key for key, _value in self.payload)
        if not self.summary.strip() or len(keys) != len(set(keys)):
            raise ValueError("drop plan requires a summary and unique payload keys")

    def payload_mapping(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self.payload))


@dataclass(frozen=True, slots=True)
class DropResolution:
    status: DropResolutionStatus
    items: tuple[DropItem, ...] = ()
    candidate: DropCandidatePlan | None = None
    diagnostics: tuple[DropDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if self.status is DropResolutionStatus.CANDIDATE and self.candidate is None:
            raise ValueError("candidate resolution requires a plan")
        if self.status is not DropResolutionStatus.CANDIDATE and self.candidate is not None:
            raise ValueError("only candidate resolutions may carry a plan")
        if self.status in {DropResolutionStatus.NEEDS_CHOICE, DropResolutionStatus.REJECTED} and not self.diagnostics:
            raise ValueError("non-candidate resolution requires a diagnostic")

    @classmethod
    def cancelled(cls) -> DropResolution:
        return cls(DropResolutionStatus.CANCELLED)


ArchiveInspector = Callable[[str, ArchivePolicy], ArchiveManifest]


def _default_archive_inspector(path: str, policy: ArchivePolicy) -> ArchiveManifest:
    return inspect_archive(path, policy=policy)


_FORMAT_KINDS: dict[FormatId, DropKind] = {
    FormatId.PLUGIN_SSE: DropKind.PLUGIN,
    FormatId.XML_EET: DropKind.EET,
    FormatId.BINARY_EET: DropKind.EET,
    FormatId.XML_XT: DropKind.XT,
    FormatId.JSON_PARATRANZ: DropKind.JSON,
    FormatId.JSON_DSD: DropKind.JSON,
    FormatId.JSON_TRANSBRIDGE: DropKind.JSON,
    FormatId.SST_SSU8: DropKind.SST,
    FormatId.SST_SSU9: DropKind.SST,
    FormatId.STRINGS: DropKind.STRINGS,
    FormatId.DLSTRINGS: DropKind.STRINGS,
    FormatId.ILSTRINGS: DropKind.STRINGS,
}
_STRINGS_SUFFIXES = {".strings", ".dlstrings", ".ilstrings"}
_ARCHIVE_SUFFIXES = {".zip", ".7z", ".rar"}
_PREFIX_PROBE_SUFFIXES = {".esp", ".esm", ".esl", ".eet", ".sst", ".xml"}
_SIGNATURE_PREFIX_BYTES = 64 * 1024


class DropRouter:
    """Produce one recoverable candidate from local paths, without dispatch."""

    def __init__(
        self,
        *,
        budget: DropBudget | None = None,
        format_catalog: FormatCatalog | None = None,
        archive_inspector: ArchiveInspector = _default_archive_inspector,
    ) -> None:
        self._budget = budget or DropBudget()
        # The catalog's built-in signature/schema probe is sufficient here.
        # Actual adapter capability remains the eventual intent owner's concern.
        self._formats = format_catalog or FormatCatalog()
        self._inspect_archive = archive_inspector
        self._archive_policy = ArchivePolicy(self._budget.archive)

    def resolve(self, paths: Iterable[str | os.PathLike[str]]) -> DropResolution:
        raw_paths = tuple(os.fspath(path) for path in paths)
        if not raw_paths:
            return self._rejected(
                "DROP_NO_LOCAL_FILES",
                "没有收到可识别的本地文件或目录。",
                "请从文件管理器拖入一个本地来源。",
            )
        if len(raw_paths) > self._budget.max_inputs:
            return self._rejected(
                "DROP_INPUT_COUNT_LIMIT",
                f"一次拖入了 {len(raw_paths)} 个对象，超过安全检查预算。",
                f"请每次选择不超过 {self._budget.max_inputs} 个对象。",
            )

        items: list[DropItem] = []
        diagnostics: list[DropDiagnostic] = []
        for raw_path in raw_paths:
            item, diagnostic = self._inspect_path(Path(raw_path))
            if item is not None:
                items.append(item)
            if diagnostic is not None:
                diagnostics.append(diagnostic)

        if diagnostics:
            status = (
                DropResolutionStatus.NEEDS_CHOICE
                if any(item.kind is DropKind.AMBIGUOUS for item in items)
                else DropResolutionStatus.REJECTED
            )
            return DropResolution(status, tuple(items), diagnostics=tuple(diagnostics))

        if len(items) != 1:
            kinds = {item.kind for item in items}
            if len(kinds) > 1:
                code = "DROP_MIXED_INPUTS"
                message = "一次拖入的对象属于不同工作流，无法安全合并为一个操作计划。"
            else:
                code = "DROP_MULTIPLE_INPUTS_CONFLICT"
                message = "一次拖入了多个来源，当前入口无法确定它们的主次关系。"
            return DropResolution(
                DropResolutionStatus.NEEDS_CHOICE,
                tuple(items),
                diagnostics=(DropDiagnostic(code, message, "请只保留一个来源后重试。"),),
            )

        item = items[0]
        return DropResolution(
            DropResolutionStatus.CANDIDATE,
            (item,),
            candidate=self._candidate_for(item),
        )

    def _inspect_path(self, path: Path) -> tuple[DropItem | None, DropDiagnostic | None]:
        display_path = os.fspath(path)
        try:
            if path.is_symlink() or _is_reparse_point(path):
                return None, DropDiagnostic(
                    "DROP_PATH_LINK",
                    "为避免目标在确认后改变，不能使用符号链接或目录联接。",
                    "请选择链接指向的真实文件或目录。",
                    display_path,
                )
            if not path.exists():
                return None, DropDiagnostic(
                    "DROP_PATH_MISSING",
                    "拖入的对象已不存在或已被移动。",
                    "重新选择仍然存在的本地对象。",
                    display_path,
                )
            resolved = path.resolve(strict=True)
        except OSError:
            return None, self._unreadable(display_path)

        if resolved.is_dir():
            return self._inspect_directory(resolved)
        if not resolved.is_file():
            return None, DropDiagnostic(
                "DROP_PATH_UNSUPPORTED_TYPE",
                "该对象不是普通文件或目录。",
                "请选择普通本地文件或 Strings 目录。",
                os.fspath(resolved),
            )
        return self._inspect_file(resolved)

    def _inspect_directory(self, path: Path) -> tuple[DropItem | None, DropDiagnostic | None]:
        try:
            entries = tuple(path.iterdir())
        except OSError:
            return None, self._unreadable(os.fspath(path))
        if len(entries) > self._budget.max_directory_entries:
            return None, DropDiagnostic(
                "DROP_DIRECTORY_ENTRY_LIMIT",
                "目录项目数超过拖放检查预算。",
                "请缩小范围或从“导入已有译文”入口选择文件。",
                os.fspath(path),
            )
        strings_files: list[Path] = []
        total = 0
        try:
            for entry in entries:
                if entry.is_symlink() or _is_reparse_point(entry):
                    return None, DropDiagnostic(
                        "DROP_DIRECTORY_LINK",
                        "目录包含符号链接或目录联接，无法安全提出导入计划。",
                        "请移除链接后重试。",
                        os.fspath(entry),
                    )
                if entry.is_file() and entry.suffix.casefold() in _STRINGS_SUFFIXES:
                    strings_files.append(entry)
                    total += entry.stat().st_size
                    if total > self._budget.max_directory_bytes:
                        return None, DropDiagnostic(
                            "DROP_DIRECTORY_SIZE_LIMIT",
                            "Strings 目录大小超过拖放检查预算。",
                            "请缩小目录范围后重试。",
                            os.fspath(path),
                        )
        except OSError:
            return None, self._unreadable(os.fspath(path))
        if path.name.casefold() != "strings" or not strings_files:
            return None, DropDiagnostic(
                "DROP_DIRECTORY_UNSUPPORTED",
                "目录不是包含本地化 Strings 文件的 Strings 目录。",
                "请选择名为 Strings 且含 .strings/.dlstrings/.ilstrings 的目录。",
                os.fspath(path),
            )
        evidence = tuple(sorted(file.suffix.casefold() for file in strings_files))
        return DropItem(os.fspath(path), DropKind.STRINGS_DIRECTORY, evidence=evidence), None

    def _inspect_file(self, path: Path) -> tuple[DropItem | None, DropDiagnostic | None]:
        suffix = path.suffix.casefold()
        if suffix == ".transbridge":
            return self._inspect_project_archive(path)
        if suffix in _ARCHIVE_SUFFIXES:
            return self._inspect_fomod_archive(path)
        try:
            size = path.stat().st_size
            oversized = size > self._budget.max_probe_bytes
            if oversized and suffix not in _PREFIX_PROBE_SUFFIXES:
                return None, DropDiagnostic(
                    "DROP_PROBE_SIZE_LIMIT",
                    "文件超过拖放格式识别预算，未读取其内容。",
                    "请从对应功能入口选择文件，以便执行完整预检。",
                    os.fspath(path),
                )
            read_limit = self._budget.max_probe_bytes
            if oversized:
                read_limit = min(read_limit, _SIGNATURE_PREFIX_BYTES)
            with path.open("rb") as source:
                content = source.read(read_limit)
        except OSError:
            return None, self._unreadable(os.fspath(path))
        source = SourceDescriptor(os.fspath(path), path.name, size)
        probe = self._formats.resolve(ProbeRequest(source, content))
        evidence = tuple(f"{item.kind.value}:{item.value}" for item in probe.evidence)
        if oversized and probe.status is not ProbeStatus.EXACT:
            return None, DropDiagnostic(
                "DROP_PROBE_SIZE_LIMIT",
                "文件超过拖放格式识别预算，有限签名不足以确认格式。",
                "请从对应功能入口选择文件，以便执行完整预检。",
                os.fspath(path),
            )
        if probe.status is ProbeStatus.AMBIGUOUS:
            return (
                DropItem(os.fspath(path), DropKind.AMBIGUOUS, probe.candidates, evidence),
                DropDiagnostic(
                    "DROP_FORMAT_AMBIGUOUS",
                    "文件同时符合多个格式，拖放无法替你选择解释方式。",
                    "请从“导入已有译文”入口选择明确格式。",
                    os.fspath(path),
                ),
            )
        if probe.status is ProbeStatus.UNSUPPORTED:
            return (
                DropItem(os.fspath(path), DropKind.UNKNOWN, evidence=evidence),
                DropDiagnostic(
                    "DROP_FORMAT_UNSUPPORTED",
                    "未识别该文件的格式签名。",
                    "请检查文件是否完整，或使用对应功能入口查看支持能力。",
                    os.fspath(path),
                ),
            )
        format_id = probe.candidates[0]
        return DropItem(os.fspath(path), _FORMAT_KINDS[format_id], (format_id,), evidence), None

    def _inspect_project_archive(self, path: Path) -> tuple[DropItem | None, DropDiagnostic | None]:
        try:
            manifest = _inspect_zip_with_policy(path, self._archive_policy)
        except (ArchivePolicyError, ArchiveExtractionError, OSError) as exc:
            return None, self._archive_diagnostic(path, exc)
        names = {member.name.casefold() for member in manifest.files}
        if "project.json" not in names:
            return None, DropDiagnostic(
                "DROP_PROJECT_ARCHIVE_INVALID",
                ".transbridge 归档缺少 project.json。",
                "请选择由 TransBridge 导出的完整工程归档。",
                os.fspath(path),
            )
        return DropItem(os.fspath(path), DropKind.PROJECT_ARCHIVE, evidence=("project.json",)), None

    def _inspect_fomod_archive(self, path: Path) -> tuple[DropItem | None, DropDiagnostic | None]:
        try:
            manifest = self._inspect_archive(os.fspath(path), self._archive_policy)
        except (ArchiveCapabilityError, ArchivePolicyError, ArchiveExtractionError, OSError) as exc:
            return None, self._archive_diagnostic(path, exc)
        evidence = tuple(member.name for member in manifest.files if _is_fomod_marker(member.name))
        if not evidence:
            return None, DropDiagnostic(
                "DROP_ARCHIVE_NOT_FOMOD",
                "归档内未发现 FOMOD/ModuleConfig.xml 或 FOMOD/info.xml。",
                "请选择受支持的 FOMOD 安装包，或从专项入口手动配置。",
                os.fspath(path),
            )
        return DropItem(os.fspath(path), DropKind.FOMOD_ARCHIVE, evidence=evidence), None

    @staticmethod
    def _candidate_for(item: DropItem) -> DropCandidatePlan:
        payload = [("path", item.path), ("drop_kind", item.kind.value)]
        if item.format_ids:
            payload.append(("format_id", item.format_ids[0].value))
        if item.kind is DropKind.PLUGIN:
            return DropCandidatePlan(IntentId.PROJECT_CREATE, tuple(payload), "使用该插件准备新建本地翻译工程")
        if item.kind is DropKind.PROJECT_ARCHIVE:
            return DropCandidatePlan(IntentId.PROJECT_IMPORT, tuple(payload), "检查并导入该 .transbridge 工程归档")
        if item.kind is DropKind.FOMOD_ARCHIVE:
            return DropCandidatePlan(IntentId.PUBLISH_FOMOD, tuple(payload), "打开 FOMOD 操作计划并使用该归档")
        return DropCandidatePlan(IntentId.SOURCE_MIGRATE, tuple(payload), "打开已有译文导入计划并使用该来源")

    @staticmethod
    def _archive_diagnostic(path: Path, exc: Exception) -> DropDiagnostic:
        if isinstance(exc, ArchivePolicyError):
            code = exc.diagnostic.code
        elif isinstance(exc, ArchiveExtractionError):
            code = exc.code
        elif isinstance(exc, ArchiveCapabilityError):
            code = str(exc).partition(":")[0]
        else:
            code = "ARCHIVE_READ_FAILED"
        return DropDiagnostic(
            f"DROP_{code}",
            "归档未通过只读安全检查，未解压也未执行任何操作。",
            "请修复归档或从 FOMOD/工程导入入口查看完整诊断。",
            os.fspath(path),
        )

    @staticmethod
    def _unreadable(path: str) -> DropDiagnostic:
        return DropDiagnostic(
            "DROP_PATH_UNREADABLE",
            "无法读取拖入的对象。",
            "请检查访问权限、文件占用和路径后重试。",
            path,
        )

    @staticmethod
    def _rejected(code: str, message: str, recovery: str) -> DropResolution:
        return DropResolution(
            DropResolutionStatus.REJECTED,
            diagnostics=(DropDiagnostic(code, message, recovery),),
        )


def _is_fomod_marker(name: str) -> bool:
    normalized = name.replace("\\", "/").strip("/").casefold()
    return normalized.endswith(("fomod/moduleconfig.xml", "fomod/info.xml"))


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _inspect_zip_with_policy(path: Path, policy: ArchivePolicy) -> ArchiveManifest:
    try:
        with zipfile.ZipFile(path) as archive:
            members = tuple(_zip_member(info) for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise ArchiveExtractionError("ARCHIVE_CORRUPT", "ZIP central directory is invalid") from exc
    return policy.evaluate(members, archive_format="zip")


def _zip_member(info: zipfile.ZipInfo) -> ArchiveMember:
    mode = (info.external_attr >> 16) & 0xFFFF
    if info.is_dir():
        member_type = ArchiveMemberType.DIRECTORY
    elif stat.S_ISLNK(mode):
        member_type = ArchiveMemberType.SYMLINK
    elif stat.S_IFMT(mode) and not stat.S_ISREG(mode):
        member_type = ArchiveMemberType.SPECIAL
    else:
        member_type = ArchiveMemberType.FILE
    return ArchiveMember(info.filename, info.file_size, info.compress_size, member_type)


__all__ = [
    "DropBudget",
    "DropCandidatePlan",
    "DropDiagnostic",
    "DropItem",
    "DropKind",
    "DropResolution",
    "DropResolutionStatus",
    "DropRouter",
]
