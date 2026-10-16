"""Column projections and model construction for workbench tables."""

from transbridge.application.terminology.models import DecisionStatus

from .paged_models import KeysetPagedTableModel, PagedColumn


def make_table_model(presenter, section: str, parent) -> KeysetPagedTableModel:
    method, columns = _definitions(section)
    read_loader = None
    try:
        loader = presenter.page_loader(method)
        if section in {"effective", "draft"}:
            browse = getattr(presenter.services.browsing, method)

            def read_loader(query, request, token):
                return browse(query, request, cancellation=token)

    except RuntimeError as error:

        def loader(_ref, _request, unavailable=error):
            raise unavailable

    return KeysetPagedTableModel(
        loader, columns, parent, page_size=100, max_cached_pages=3, manual_paging=True, read_loader=read_loader
    )


def _definitions(section):
    if section == "conflicts":
        return "list_conflicts", (
            PagedColumn("original", "原名", lambda item: item.normalized_original),
            PagedColumn("variants", "不同译法", lambda item: len(item.variants)),
            PagedColumn("risk", "处理优先级", lambda item: _risk_label(item.risk)),
            PagedColumn("status", "状态", lambda item: _conflict_status(item.status)),
        )
    if section == "history":
        return "list_versions", (
            PagedColumn("version", "版本", lambda item: item.version_id),
            PagedColumn("digest", "内容", lambda _item: "已发布项目术语"),
        )
    return ("list_effective_terms" if section == "effective" else "list_browsable_draft_terms"), (
        PagedColumn("original", "原名", lambda item: item.original),
        PagedColumn("translation", "生效译名" if section == "effective" else "草稿译名", lambda item: item.translation),
        PagedColumn("scope", "使用范围", lambda item: scope_label(item.scope)),
        PagedColumn("state", "状态", (lambda _item: "当前生效") if section == "effective" else decision_status_label),
    )


def scope_label(scope: object) -> str:
    plugin_id = getattr(scope, "plugin_id", None)
    return "整个项目" if plugin_id is None else f"仅在 {plugin_id} 中使用"


def decision_status_label(item: object) -> str:
    if getattr(item, "suppressed", False):
        return "已停用 · 发布后生效"
    if getattr(item, "status", None) is DecisionStatus.REVIEW_REQUIRED:
        return "待复核"
    if getattr(item, "status", None) is DecisionStatus.UNRESOLVED:
        return "待决定"
    return "已确认 · 待发布"


def _risk_label(value: object) -> str:
    return {"high": "优先处理", "medium": "一般", "low": "较低"}.get(str(value), str(value))


def _conflict_status(value: object) -> str:
    return {"unresolved": "待决定", "unified": "已统一", "plugin_exception": "按插件分别使用", "ignored": "已排除"}.get(
        str(value), str(value)
    )
