"""Project complete capture results and failed checks without inventing context."""

from __future__ import annotations

from pathlib import PurePath

from transbridge.application.contracts import Diagnostic, RequestContext
from transbridge.application.terminology.input_capture import BuildInputSnapshot

from .view_models import SourceScopeViewState, TechnicalDetail, TerminologyPreflightViewState, business_diagnostic


def preflight_ready(snapshot: BuildInputSnapshot, context: RequestContext) -> TerminologyPreflightViewState:
    sources = tuple(_source_view(source) for source in snapshot.sources)
    metadata = dict(context.metadata)
    project_name = metadata.get("project_name") or "项目名称待确认"
    variant_name = metadata.get("variant_name") or "翻译版本名称待确认"
    action_label = "更新术语库" if snapshot.effective_version_id else "创建术语库"
    return TerminologyPreflightViewState(
        True,
        f"可以{action_label}",
        "将读取当前工程已登记且启用的来源，并包含当前翻译版本尚未写回的调整。",
        project_label=f"当前工程 · {project_name}",
        variant_label=f"翻译版本 · {variant_name}",
        scope_label=f"来源范围 · {len(sources)} 个已启用来源",
        current_version_label=(
            "当前版本 · 尚无已发布版本"
            if snapshot.effective_version_id is None
            else f"当前版本 · {snapshot.effective_version_id}"
        ),
        project_display_name=project_name,
        variant_display_name=variant_name,
        current_version_value=snapshot.effective_version_id or "",
        sources=sources,
        source_count=len(sources),
        technical_details=(
            TechnicalDetail("工程标识", snapshot.project_id),
            TechnicalDetail("翻译版本标识", snapshot.variant_id),
            TechnicalDetail("工程修订", str(snapshot.project_revision)),
            TechnicalDetail("翻译版本修订", str(snapshot.variant_revision)),
            TechnicalDetail("当前术语版本", snapshot.effective_version_id or "尚无已发布版本"),
            TechnicalDetail("配置摘要", snapshot.config_digest),
        ),
        action_label=action_label,
    )


def preflight_failed(
    context: RequestContext, diagnostic: Diagnostic | None = None, *, message: str = ""
) -> TerminologyPreflightViewState:
    code = "TERMINOLOGY_PREFLIGHT_FAILED" if diagnostic is None else diagnostic.code
    details = {} if diagnostic is None else dict(diagnostic.details)
    count = details.get("source_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        count = 0 if code in {"TERMINOLOGY_SOURCE_REQUIRED", "SOURCE_REQUIRED"} else None
    notice = business_diagnostic(code, message if diagnostic is None else diagnostic.message)
    title = notice.title
    explanation = f"{notice.message} {notice.impact} {notice.recovery}".strip()
    can_import = context.project_id is not None and context.variant_id is not None
    if code == "TERMINOLOGY_STREAMING_REQUIRED":
        title, explanation = _source_limit_copy(details, count)
        if can_import:
            explanation += "也可以直接导入已有术语，不必先从项目提取。"
    elif code in {"TERMINOLOGY_SOURCE_REQUIRED", "SOURCE_REQUIRED"}:
        explanation = "当前项目没有已启用的来源。请先添加并启用项目来源，再重新检查；已有历史保持不变。"
        if can_import:
            explanation += "已有术语可直接导入。"
    elif title == "操作未完成":
        title = "暂时无法从项目提取术语"
        explanation = f"{notice.message} 请检查项目来源后重试。"
        if can_import:
            explanation += "已有术语仍可直接导入。"
    metadata = dict(context.metadata)
    project_name = metadata.get("project_name") or "项目名称待确认" if context.project_id is not None else "未打开工程"
    variant_name = (
        metadata.get("variant_name") or "翻译版本名称待确认" if context.variant_id is not None else "未选择翻译版本"
    )
    return TerminologyPreflightViewState(
        False,
        title,
        explanation,
        project_label=f"当前工程 · {project_name}",
        variant_label=f"翻译版本 · {variant_name}",
        scope_label="来源范围 · 尚未确认" if count is None else f"来源范围 · {count} 个已启用来源",
        current_version_label="当前版本 · 尚未确认",
        project_display_name=project_name,
        variant_display_name=variant_name,
        source_count=count,
        diagnostic_code=code,
        technical_details=(
            TechnicalDetail("工程标识", context.project_id or "未打开"),
            TechnicalDetail("翻译版本标识", context.variant_id or "未选择"),
            *(
                ()
                if diagnostic is None
                else (
                    TechnicalDetail("诊断代码", diagnostic.code),
                    TechnicalDetail("诊断信息", diagnostic.message),
                    *(TechnicalDetail(str(key), str(value)) for key, value in diagnostic.details),
                )
            ),
        ),
    )


def _source_limit_copy(details, count):
    limit = details.get("limit_count")
    if count is not None and isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
        return (
            "项目来源较多，暂不能提取",
            f"当前有 {count} 个启用来源，本次最多可提取 {limit} 个。请减少本次启用的项目来源后重新检查。",
        )
    return (
        "项目来源文件较大，暂不能提取",
        "来源文件超过本次可读取的大小。请拆分或减少项目来源后重新检查。",
    )


def _source_view(source) -> SourceScopeViewState:
    registration = source.registration
    location = registration.location
    name = registration.display_name or PurePath(location.replace("\\", "/")).name or registration.source_id
    return SourceScopeViewState(
        registration.source_id,
        name,
        registration.format_id.value,
        (
            TechnicalDetail("来源标识", registration.source_id),
            TechnicalDetail("位置", location),
            TechnicalDetail("内容指纹", source.lease.actual_fingerprint),
            TechnicalDetail("适配器", f"{source.adapter_id} {source.adapter_version}"),
        ),
    )
