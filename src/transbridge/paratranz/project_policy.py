# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read the bound project's source attachment and publish Owner edits entirely in memory."""

from dataclasses import dataclass
import json

from transbridge.application.file_upload_modes import UploadOperation
from transbridge.application.upload_policy import (
    POLICY_FILENAME,
    MemberSelectors,
    OperationRule,
    ProjectRole,
    TranslationMode,
    UploadLayout,
    UploadPolicy,
)
from transbridge.application.upload_policy_codec import parse_upload_policy, upload_policy_entries

from .api.paratranz_members_api import ParatranzMembersAPI
from .api.paratranz_project_api import ParatranzProjectAPI
from .api.paratranz_user_api import ParatranzUserAPI
from .file_publication import FilePublicationGateway
from .file_transport import _items


class ProjectUploadGateway(FilePublicationGateway, ParatranzMembersAPI, ParatranzProjectAPI, ParatranzUserAPI):
    """Use the upload session's target, cancellation and request budget for policy requests."""


def default_upload_policy():
    everyone = OperationRule(allow=MemberSelectors(project_member=True))
    return UploadPolicy(UploadLayout.USER_CHOICE, frozenset(TranslationMode), dict.fromkeys(UploadOperation, everyone))


@dataclass(frozen=True, slots=True)
class PolicyAccess:
    file_id: int | None
    policy: UploadPolicy | None
    user_id: int
    role: ProjectRole | None
    members: tuple[tuple[int, str], ...]

    def allows_layout(self, categorized):
        layout = UploadLayout.CATEGORIZED if categorized else UploadLayout.FLAT
        return self.policy is None or self.policy.layout in (UploadLayout.USER_CHOICE, layout)

    def allows(self, mode):
        return self.operation_denial(mode) is None

    def operation_denial(self, mode):
        if self.policy is None:
            return None
        return self.policy.operation_denial(UploadOperation(mode), user_id=self.user_id, role=self.role)


def _source(gateway, project_id, paths):
    ids = [value for path, value in paths.items() if path.casefold() == POLICY_FILENAME]
    if not ids:
        return None, None
    if len(ids) != 1:
        raise ValueError("项目根目录有多个同名政策文件。")
    data = gateway.get_file_content(project_id, ids[0], dl=True)
    if not isinstance(data, bytes):
        raise ValueError("未收到项目政策原文附件。")
    try:
        text = data.decode("utf-8-sig").strip()
        rows = json.loads(text) if text else []
        policy = None if rows == [] else parse_upload_policy(rows)
    except ValueError as exc:
        raise ValueError(f"无法读取 {POLICY_FILENAME}：{exc}") from exc
    return ids[0], policy


def read_access(gateway, project_id, user_id, paths, *, for_window=False):
    file_id, policy = _source(gateway, project_id, paths)
    if not for_window and policy is None:
        return PolicyAccess(file_id, None, user_id, None, ())
    me = gateway.get_my_user()
    project = gateway.get_project(project_id)
    if not isinstance(me, dict) or me.get("id") != user_id:
        raise ValueError("当前 ParaTranz 账号与工程绑定账号不一致。")
    if not isinstance(project, dict) or project.get("id") != project_id or type(project.get("uid")) is not int:
        raise ValueError("无法确认目标 ParaTranz 项目及其所有者。")
    owner = project["uid"] == user_id
    role = ProjectRole.OWNER if owner else None
    names = {user_id: me.get("nickname") or me.get("username") or str(user_id)} if owner else {}
    if (owner and for_window) or (not owner and policy is not None):
        rows = _items(gateway.list_members(project_id), "results", "data", "items")
        for row in rows:
            if not isinstance(row, dict) or type(uid := row.get("uid")) is not int:
                raise ValueError("项目成员缺少有效账号 ID。")
            user = row.get("user") or {}
            names[uid] = user.get("nickname") or user.get("username") or str(uid)
            if uid == user_id and not owner:
                role = {1: ProjectRole.TRANSLATOR, 2: ProjectRole.PROOFREADER, 3: ProjectRole.ADMIN}.get(
                    row.get("permission")
                )
    return PolicyAccess(file_id, policy, user_id, role, tuple(names.items()))


def publish_policy(gateway, project_id, previous, policy, check_target):
    current = read_access(gateway, project_id, previous.user_id, gateway.inventory(project_id), for_window=True)
    if current.role != ProjectRole.OWNER:
        raise PermissionError("仅项目所有者可以修改上传规则。")
    if current.policy == policy:
        return current
    if (current.file_id, current.policy) != (previous.file_id, previous.policy):
        raise ValueError("云端政策已变更，请重新打开上传窗口。")
    content = json.dumps(upload_policy_entries(policy), ensure_ascii=False, indent=2).encode("utf-8")
    check_target()
    if current.file_id is None:
        gateway.upload_file_bytes(project_id, POLICY_FILENAME, content)
    else:
        gateway.update_file_bytes(project_id, current.file_id, POLICY_FILENAME, content, incremental=False)
    verified = read_access(gateway, project_id, current.user_id, gateway.inventory(project_id), for_window=True)
    if verified.policy != policy:
        raise ValueError("云端政策读回与本次设置不一致，请重新读取。")
    return verified
