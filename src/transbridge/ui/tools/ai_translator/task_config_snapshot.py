"""Secret-free execution drafts, restored without writing global preferences."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import fields, is_dataclass
from enum import Enum


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
