"""
后处理提示词模板契约工具。

为后处理四个阶段（quality_gate / refinement / polish / arbitration）×
请求形态（single / batch）的提示词建立可独立审查的稳定 SYSTEM 契约：

- 严格校验 TOML 模板占位符，禁止未知变量与缺失 required 变量。
- 正常渲染使用严格 Template.substitute()，不得用 safe_substitute() 隐藏变量遗漏。
- 单变体验证失败 -> 回退到对应内置默认模板；其他变体不受影响。
- 计算阶段级独立 cache key；组装 SYSTEM(FINAL) -> USER 两条消息。
"""

from __future__ import annotations

from collections.abc import Mapping, Set
import hashlib
import logging
import re
from string import Template
from typing import Literal

from transbridge.infra.prompt_cache import (
    attach_prompt_cache_directive,
)

logger = logging.getLogger(__name__)

PostProcessStage = Literal[
    "quality_gate",
    "refinement",
    "polish",
    "arbitration",
]
PromptShape = Literal["single", "batch"]

# SYSTEM 只允许稳定变量；动态内容（条目、术语、问题、置信度、运行设置）不得进入 System。
_SYSTEM_ALLOWED_VARIABLES = frozenset({"game_name", "source_lang", "target_lang"})

# 未解析占位符匹配：$identifier / ${identifier}
_UNRESOLVED_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class PromptTemplateContractError(ValueError):
    """模板违反提示词契约。"""


def _identifiers(template: str) -> set[str]:
    parsed = Template(template)
    if not parsed.is_valid():
        raise ValueError("invalid string.Template syntax")
    return set(parsed.get_identifiers())


def validate_prompt_template(
    *,
    name: str,
    template: str,
    allowed_variables: Set[str],
    required_variables: Set[str],
) -> None:
    """校验模板占位符合法；违规抛 PromptTemplateContractError。"""
    try:
        ids = _identifiers(template)
    except ValueError as exc:
        raise PromptTemplateContractError(f"{name}: 模板语法无效") from exc
    unknown = ids - set(allowed_variables)
    if unknown:
        raise PromptTemplateContractError(f"{name}: 模板包含未知变量 {sorted(unknown)}")
    missing = set(required_variables) - ids
    if missing:
        raise PromptTemplateContractError(f"{name}: 模板缺少 required 变量 {sorted(missing)}")


def render_prompt_template(
    *,
    name: str,
    template: str,
    values: Mapping[str, object],
) -> str:
    """严格渲染模板。未知/缺失变量抛错误，不得静默保留占位符。"""
    try:
        result = Template(template).substitute(values)
    except KeyError as exc:
        raise PromptTemplateContractError(f"{name}: 缺少变量 {exc.args[0]!r}") from exc
    except ValueError as exc:
        raise PromptTemplateContractError(f"{name}: 模板语法无效") from exc
    unresolved = {match.group(1) or match.group(2) for match in _UNRESOLVED_PATTERN.finditer(result)}
    if unresolved:
        raise PromptTemplateContractError(f"{name}: 渲染后存在未解析占位符 {sorted(unresolved)}")
    return result


def build_postprocess_cache_key(
    *,
    stage: PostProcessStage,
    shape: PromptShape,
    rendered_system: str,
) -> str:
    """阶段独立 key：stage + shape + 完整渲染 System 决定。

    动态内容不进入 key；change game/语言对（进入 System）自然产生新 key。
    """
    digest = hashlib.sha256(rendered_system.encode("utf-8")).hexdigest()[:24]
    return f"transbridge.postprocess.v1.{stage}.{shape}.{digest}"


def build_postprocess_messages(
    *,
    stage: PostProcessStage,
    shape: PromptShape,
    rendered_system: str,
    user_content: str,
) -> list[dict]:
    """组装 SYSTEM(FINAL) -> USER，并为 SYSTEM 挂 single_stable_prefix/FINAL 指令。"""
    key = build_postprocess_cache_key(
        stage=stage,
        shape=shape,
        rendered_system=rendered_system,
    )
    system_msg = attach_prompt_cache_directive(
        {"role": "system", "content": rendered_system},
        cache_key=key,
        profile="single_stable_prefix",
        breakpoint="FINAL",
    )
    return [system_msg, {"role": "user", "content": user_content}]


__all__ = [
    "PromptTemplateContractError",
    "PostProcessStage",
    "PromptShape",
    "validate_prompt_template",
    "render_prompt_template",
    "build_postprocess_cache_key",
    "build_postprocess_messages",
]
