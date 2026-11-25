# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""ParaTranz v1 upload policy values for an already identified project member."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar

from .file_upload_modes import UploadOperation

POLICY_FILENAME = "transbridge-policy.json"


class UploadLayout(StrEnum):
    USER_CHOICE = "user_choice"
    FLAT = "flat"
    CATEGORIZED = "categorized"


class TranslationMode(StrEnum):
    FILL_EMPTY = "fill_empty"
    SAFE_IMPORT = "safe_import"
    FORCE = "force"


class ProjectRole(StrEnum):
    TRANSLATOR = "translator"
    PROOFREADER = "proofreader"
    ADMIN = "admin"
    OWNER = "owner"


_REQUIRED_TRANSLATION_MODE = {
    UploadOperation.ORIGINAL: None,
    UploadOperation.FILL_EMPTY: TranslationMode.FILL_EMPTY,
    UploadOperation.SAFE: TranslationMode.SAFE_IMPORT,
    UploadOperation.BOTH: TranslationMode.SAFE_IMPORT,
    UploadOperation.FORCE: TranslationMode.FORCE,
}


@dataclass(frozen=True, slots=True)
class MemberSelectors:
    user_ids: frozenset[int] = frozenset()
    roles: frozenset[ProjectRole] = frozenset()
    project_member: bool = False

    def matches(self, user_id: int, role: ProjectRole) -> bool:
        return self.project_member or user_id in self.user_ids or role in self.roles


@dataclass(frozen=True, slots=True)
class OperationRule:
    allow: MemberSelectors = MemberSelectors()
    deny: MemberSelectors = MemberSelectors()


@dataclass(frozen=True, slots=True)
class UploadPolicy:
    version: ClassVar[str] = "1"
    layout: UploadLayout
    translation_modes: frozenset[TranslationMode]
    rules: Mapping[UploadOperation, OperationRule]

    def __post_init__(self) -> None:
        if not self.translation_modes:
            raise ValueError("upload.translation_mode 至少需要一种译文写入方式。")
        rules = dict.fromkeys(UploadOperation, OperationRule())
        rules.update(self.rules)
        object.__setattr__(self, "rules", MappingProxyType(rules))

    def permits_operation(self, operation: UploadOperation, *, user_id: int, role: ProjectRole | None) -> bool:
        """A missing verified project role grants nothing. Platform permission remains a caller check."""
        return self.operation_denial(operation, user_id=user_id, role=role) is None

    def operation_denial(self, operation: UploadOperation, *, user_id: int, role: ProjectRole | None) -> str | None:
        required_mode = _REQUIRED_TRANSLATION_MODE[operation]
        if required_mode is not None and required_mode not in self.translation_modes:
            name = {
                TranslationMode.FILL_EMPTY: "填补云端空译文",
                TranslationMode.SAFE_IMPORT: "安全导入译文",
                TranslationMode.FORCE: "强制导入译文",
            }[required_mode]
            return f"项目未开放「{name}」。"
        if not isinstance(role, ProjectRole) or type(user_id) is not int or user_id < 1:
            return "无法确认当前账号在此项目中的成员身份。"
        rule = self.rules[operation]
        if rule.deny.matches(user_id, role) or not rule.allow.matches(user_id, role):
            return "当前账号未获此操作的授权。"
        return None
