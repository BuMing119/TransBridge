# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read and write decoded ParaTranz source rows for the project upload policy."""

from enum import StrEnum

from .file_upload_modes import UploadOperation
from .upload_policy import MemberSelectors, OperationRule, ProjectRole, TranslationMode, UploadLayout, UploadPolicy

_PROJECT_KEYS = ("policy.version", "upload.layout", "upload.translation_mode")
_RULE_KEYS = {f"upload.{op}{suffix}" for op in UploadOperation for suffix in ("", ".deny")}


def _enum_value[T: StrEnum](enum_type: type[T], value: str, key: str) -> T:
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"{key} 的值 {value!r} 无效，可选值为 {', '.join(enum_type)}。") from exc


def _selectors(value: str, key: str) -> MemberSelectors:
    if value == "none":
        return MemberSelectors()
    users: set[int] = set()
    roles: set[ProjectRole] = set()
    project_member = False
    for item in value.split(","):
        token = item.strip()
        kind, _, identifier = token.partition(":")
        if token == "project_member":
            project_member = True
        elif kind == "role":
            roles.add(_enum_value(ProjectRole, identifier, key))
        elif kind == "user" and identifier.isascii() and identifier.isdecimal():
            try:
                user_id = int(identifier)
            except ValueError as exc:
                raise ValueError(f"{key} 的账号 ID 无效。") from exc
            if user_id < 1:
                raise ValueError(f"{key} 的账号 ID 必须为正整数。")
            users.add(user_id)
        else:
            raise ValueError(f"{key} 的名单项 {token!r} 无效。")
    return MemberSelectors(frozenset(users), frozenset(roles), project_member)


def parse_upload_policy(entries: object) -> UploadPolicy:
    """Parse one complete decoded JSON array. Any invalid source row rejects the entire policy."""
    if not isinstance(entries, list):
        raise ValueError("政策文件必须为 JSON 词条数组。")
    values = {}
    for index, row in enumerate(entries, 1):
        if not isinstance(row, dict) or not isinstance(key := row.get("key"), str):
            raise ValueError(f"政策文件第 {index} 条缺少有效 key。")
        if key in values:
            raise ValueError(f"政策文件第 {index} 条的 key {key!r} 重复。")
        if key not in _PROJECT_KEYS and key not in _RULE_KEYS:
            raise ValueError(f"政策文件第 {index} 条的 key {key!r} 未知。")
        original = row.get("original")
        if not isinstance(original, str) or not original.strip():
            raise ValueError(f"{key} 的 original 必须为非空字符串。")
        values[key] = original.strip()
    for key in _PROJECT_KEYS:
        if key not in values:
            raise ValueError(f"政策文件缺少 {key}。")
    if values["policy.version"] != UploadPolicy.version:
        raise ValueError(f"不支持的 policy.version：{values['policy.version']!r}。")
    layout = _enum_value(UploadLayout, values["upload.layout"], "upload.layout")
    modes = frozenset(
        _enum_value(TranslationMode, item.strip(), "upload.translation_mode")
        for item in values["upload.translation_mode"].split(",")
    )
    rules = {}
    for operation in UploadOperation:
        key = f"upload.{operation}"
        rules[operation] = OperationRule(
            _selectors(values.get(key, "none"), key),
            _selectors(values.get(f"{key}.deny", "none"), f"{key}.deny"),
        )
    return UploadPolicy(layout, modes, rules)


def _selector_tokens(selectors: MemberSelectors) -> list[str]:
    tokens = ["project_member"] if selectors.project_member else []
    tokens.extend(f"role:{role}" for role in sorted(selectors.roles))
    tokens.extend(f"user:{user_id}" for user_id in sorted(selectors.user_ids))
    return tokens


def upload_policy_entries(policy: UploadPolicy) -> list[dict[str, str]]:
    """Produce canonical source rows; omitted empty lists have the same meaning as 'none'."""
    values = {
        "policy.version": policy.version,
        "upload.layout": policy.layout.value,
        "upload.translation_mode": ",".join(mode for mode in TranslationMode if mode in policy.translation_modes),
    }
    for operation in UploadOperation:
        rule = policy.rules[operation]
        key = f"upload.{operation}"
        for name, selectors in ((key, rule.allow), (f"{key}.deny", rule.deny)):
            if tokens := _selector_tokens(selectors):
                values[name] = ",".join(tokens)
    return [{"key": key, "original": value, "translation": ""} for key, value in values.items()]
