"""Secret-free execution drafts, restored without writing global preferences."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import fields, is_dataclass
from enum import Enum
from hashlib import sha256
import json

_OMITTED = frozenset({
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "credential_ref",
    "credentials",
    "max_concurrent",
    "llm_max_retries",
    "config_revision",
})


def _signature_value(value):
    if is_dataclass(value):
        value = {field.name: getattr(value, field.name) for field in fields(value)}
    elif hasattr(value, "__dict__") and not isinstance(value, Enum):
        value = vars(value)
    if isinstance(value, Mapping):
        return {
            str(key): _signature_value(item)
            for key, item in value.items()
            if not str(key).startswith(("_", "assistant_", "mcp_"))
            and str(key).lower() not in _OMITTED
            and not any(word in str(key).lower() for word in ("credential", "password", "secret", "api_key"))
        }
    if isinstance(value, (set, frozenset)):
        return [_signature_value(item) for item in sorted(value, key=str)]
    if isinstance(value, (tuple, list)):
        return [_signature_value(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"无法为恢复任务计算配置签名：{type(value).__name__}")


def execution_signature(config) -> str:
    """Hash execution settings without credentials, retaining numeric token limits."""
    payload = json.dumps(_signature_value(config), ensure_ascii=False, sort_keys=True, allow_nan=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def _excluded(name):
    name = str(name).lower()
    return (
        name.startswith(("_", "assistant_", "mcp_"))
        or name
        in {
            "token",
            "access_token",
            "refresh_token",
            "authorization",
            "cookie",
            "headers",
            "config_revision",
        }
        or any(word in name for word in ("credential", "password", "secret", "api_key", "apikey"))
    )


def execution_snapshot(value):
    if is_dataclass(value):
        value = {field.name: getattr(value, field.name) for field in fields(value)}
    elif hasattr(value, "__dict__") and not isinstance(value, Enum):
        value = vars(value)
    if isinstance(value, Mapping):
        return {str(key): execution_snapshot(item) for key, item in value.items() if not _excluded(key)}
    if isinstance(value, (set, frozenset)):
        return [execution_snapshot(item) for item in sorted(value, key=str)]
    if isinstance(value, (tuple, list)):
        return [execution_snapshot(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"任务配置无法保存：{type(value).__name__}")


def restore_execution_snapshot(current, snapshot):
    """Keep current credential objects, replacing only declared execution fields."""
    if not isinstance(snapshot, dict):
        raise ValueError("恢复任务配置格式无效。")
    restored = current.copy_for_execution() if hasattr(current, "copy_for_execution") else deepcopy(current)
    for name, value in snapshot.items():
        if _excluded(name) or not hasattr(restored, name):
            raise ValueError(f"恢复任务配置字段无效：{name}")
        before = getattr(restored, name)
        if is_dataclass(before):
            value = restore_execution_snapshot(before, value)
        elif name == "action_rules":
            from transbridge.paratranz.config_manager import ActionRule

            value = [ActionRule.from_dict(item) for item in value]
        else:
            value = deepcopy(value)
        setattr(restored, name, value)
    return restored
