"""Deterministic mapping from business projections to user guidance."""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    GuidanceIntent,
    GuidanceIntentId,
    GuidanceKind,
    GuidanceProjection,
    GuidanceState,
)


@dataclass(frozen=True, slots=True)
class _StateTemplate:
    headline: str
    reason: str
    primary: GuidanceIntent
    recovery: tuple[GuidanceIntent, ...]
    details: tuple[str, ...] = ()


_TEMPLATES = {
    GuidanceKind.NO_PROJECT: _StateTemplate(
        "新建本地翻译工程",
        "当前没有打开的本地翻译工程。",
        GuidanceIntent(GuidanceIntentId.PROJECT_CREATE, "新建工程"),
        (GuidanceIntent(GuidanceIntentId.PROJECT_OPEN, "打开已有本地工程"),),
        ("进入建项页后可以选择插件，或使用高级入口创建空工程。",),
    ),
    GuidanceKind.EMPTY_PROJECT: _StateTemplate(
        "为当前工程添加插件",
        "当前工程还没有可编辑的翻译内容。",
        GuidanceIntent(GuidanceIntentId.WORKBENCH_CONTENT_PREPARE, "选择插件"),
        (GuidanceIntent(GuidanceIntentId.PROJECT_CREATE, "新建其他工程"),),
        ("选择 ESP、ESM 或 ESL，将它加载为当前工程的翻译内容。",),
    ),
    GuidanceKind.UNTRANSLATED: _StateTemplate(
        "开始翻译未完成的内容",
        "当前翻译内容中仍有尚未翻译的词条。",
        GuidanceIntent(GuidanceIntentId.TRANSLATION_AI_RUN, "开始 AI 翻译"),
        (GuidanceIntent(GuidanceIntentId.TRANSLATION_IMPORT_SOURCE, "导入已有译文"),),
        ("运行前可以确认作用域、估算和覆盖策略。",),
    ),
    GuidanceKind.REVIEW_PENDING: _StateTemplate(
        "检查翻译问题",
        "翻译内容中有需要检查或确认的词条。",
        GuidanceIntent(GuidanceIntentId.TRANSLATION_REVIEW, "检查问题"),
        (GuidanceIntent(GuidanceIntentId.TASK_OPEN_ACTIVITY, "查看相关任务与结果"),),
        ("检查入口会保留当前筛选、选择和词条位置。",),
    ),
    GuidanceKind.PUBLISH_PENDING: _StateTemplate(
        "写回或发布翻译结果",
        "翻译内容已准备好，但尚未写回或发布。",
        GuidanceIntent(GuidanceIntentId.PUBLISH_WRITE, "检查并写回"),
        (GuidanceIntent(GuidanceIntentId.SYNC_PARATRANZ_UPLOAD, "上传至 ParaTranz"),),
        ("正式副作用前会先显示输出计划和预检结果。",),
    ),
    GuidanceKind.MISSING_CONFIGURATION: _StateTemplate(
        "补齐所需服务配置",
        "当前动作缺少必需的服务配置。",
        GuidanceIntent(GuidanceIntentId.SETTINGS_SERVICES, "修复服务配置"),
        (GuidanceIntent(GuidanceIntentId.TRANSLATION_IMPORT_SOURCE, "改为导入已有译文"),),
        ("本地浏览、编辑和导入能力不会被无关的云端配置阻塞。",),
    ),
    GuidanceKind.FAILED: _StateTemplate(
        "处理失败的任务",
        "最近一次任务未完成。",
        GuidanceIntent(GuidanceIntentId.TASK_RETRY, "修复后重试"),
        (GuidanceIntent(GuidanceIntentId.TASK_OPEN_ACTIVITY, "查看错误、日志与结果"),),
        ("重试会重新预检并创建新的 Run ID。",),
    ),
    GuidanceKind.PARTIAL_FAILURE: _StateTemplate(
        "处理未完成的项目",
        "任务只完成了部分对象，仍有失败项需要处理。",
        GuidanceIntent(GuidanceIntentId.TASK_RETRY, "仅重试失败项"),
        (GuidanceIntent(GuidanceIntentId.TASK_OPEN_ACTIVITY, "查看成功与失败对象"),),
        ("重试失败项前会重新预检，并使用新的 Run ID。",),
    ),
}


def build_guidance_state(projection: GuidanceProjection) -> GuidanceState:
    """Build one state without inspecting widgets or application internals."""

    template = _TEMPLATES[projection.kind]
    reason = projection.reason.strip() or template.reason
    primary = template.primary
    recovery = template.recovery
    details = template.details

    if projection.kind is GuidanceKind.MISSING_CONFIGURATION:
        missing = "、".join(projection.missing_configuration)
        reason = projection.reason.strip() or f"缺少配置：{missing}。"
        details = (f"需要配置：{missing}。", *details)
    elif projection.kind in {GuidanceKind.FAILED, GuidanceKind.PARTIAL_FAILURE} and not projection.retry_available:
        primary = GuidanceIntent(GuidanceIntentId.TASK_OPEN_ACTIVITY, "查看失败详情")
        recovery = (GuidanceIntent(GuidanceIntentId.PROJECT_OPEN, "打开所属工程"),)
        details = ("当前任务没有可验证的重试工厂，因此不会显示虚假的重试动作。",)

    return GuidanceState(
        context_identity=projection.context_identity,
        generation=projection.generation,
        revision=projection.revision,
        kind=projection.kind,
        headline=template.headline,
        reason=reason,
        primary_intent=primary,
        recovery_intents=recovery,
        detail_lines=details,
    )


__all__ = ["build_guidance_state"]
