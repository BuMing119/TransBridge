"""Prepare snapshot-bound sync plans and actionable preflight projections."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging

from transbridge.application.ports.paratranz import ExternalServiceError
from transbridge.application.sync import CreateSyncPlanRequest, ParaTranzSyncPlanningUseCase, SyncOperation, SyncPlanner
from transbridge.application.sync.download_review import DownloadDecision, DownloadDecisionStaleError
from transbridge.application.sync.upload_review import UploadDecision, UploadDecisionStaleError
from transbridge.paratranz.service import ParaTranzService
from transbridge.paratranz.sync_diagnostics import log_sync_diagnostics
from transbridge.paratranz.sync_snapshot import ParaTranzRemoteSnapshotAdapter

from .mappers import DomainPreflightResult
from .preflight_view import PreflightCheckState, PreflightCheckStatus
from .production_support import SyncRequest, blocked, sync_request_target_is_current
from .sync_review_projection import download_impact, review_rows, upload_impact

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedSync:
    request: SyncRequest
    planning: ParaTranzSyncPlanningUseCase
    plan: object
    service: object
    project_name: str

    def close(self) -> None:
        close = getattr(self.service, "close", None)
        if callable(close):
            close()


def prepare_sync_request(value: object) -> tuple[PreparedSync | None, DomainPreflightResult]:
    if not isinstance(value, SyncRequest):
        _logger.warning("PARATRANZ_SYNC_CHECK_FAILED code=SYNC_REQUEST_INVALID")
        return None, DomainPreflightResult((blocked("SYNC_REQUEST_INVALID", "ParaTranz 请求无效"),))
    current, reason = sync_request_target_is_current(value)
    if not current:
        _logger.warning(
            "PARATRANZ_SYNC_CHECK_FAILED project_id=%s operation=%s code=PARATRANZ_TARGET_STALE",
            value.project_id,
            value.operation.value,
        )
        return None, DomainPreflightResult((blocked("PARATRANZ_TARGET_STALE", reason),))
    service = None
    try:
        service = ParaTranzService.from_config(value.ui_context.config)
        if value.target_account_user_id is None:
            raise PermissionError("当前 ParaTranz 账号尚未验证")
        mine = service.list_projects(uid=value.target_account_user_id)
        remote_project = next((item for item in mine if item.project_id == value.project_id), None)
        if remote_project is None:
            target_name = value.target_project_name or "所选 ParaTranz 项目"
            raise PermissionError(f"当前账号无权访问“{target_name}”")
        planning = ParaTranzSyncPlanningUseCase(
            ParaTranzRemoteSnapshotAdapter(service),
            planner=SyncPlanner(
                local_state_only=bool(getattr(value.ui_context, "uses_authoritative_projection", False))
            ),
        )
        plan_request = CreateSyncPlanRequest(
            value.project_id,
            value.namespace,
            value.local_entries,
            value.operation,
            value.conflict_policy,
            value.deletion_policy,
            download_decisions=tuple(DownloadDecision(*item) for item in value.download_decisions),
            upload_decisions=tuple(UploadDecision(*item) for item in value.upload_decisions),
        )
        stale = False
        try:
            plan = planning.create_plan(plan_request)
        except (DownloadDecisionStaleError, UploadDecisionStaleError):
            stale = True
            plan = planning.create_plan(replace(plan_request, download_decisions=(), upload_decisions=()))
    except Exception as exc:
        if service is not None:
            service.close()
        if isinstance(exc, PermissionError):
            check = blocked("PARATRANZ_MEMBER_REQUIRED", str(exc))
        elif isinstance(exc, ExternalServiceError):
            check = blocked(
                f"PARATRANZ_{exc.category.value.upper()}",
                f"ParaTranz 检查失败：{_external_error_label(exc.category.value)}",
            )
        else:
            _logger.exception("Failed to prepare the ParaTranz synchronization plan")
            check = blocked("PARATRANZ_PREFLIGHT_FAILED", "无法完成 ParaTranz 检查，请稍后重试")
        _logger.warning(
            "PARATRANZ_SYNC_CHECK_FAILED project_id=%s operation=%s code=%s",
            value.project_id,
            value.operation.value,
            check.check_id,
        )
        return None, DomainPreflightResult((check,))
    try:
        log_sync_diagnostics(
            plan,
            project_id=value.project_id,
            local_count=len(value.local_entries),
            stale_decisions=stale,
        )
    except Exception:
        _logger.exception(
            "PARATRANZ_SYNC_DIAGNOSTICS_FAILED project_id=%s operation=%s plan_id=%s",
            value.project_id,
            value.operation.value,
            plan.plan_id,
        )
    prepared = PreparedSync(value, planning, plan, service, remote_project.name)
    download = value.operation is SyncOperation.DOWNLOAD
    unresolved = plan.conflicts
    check = PreflightCheckState(
        "PARATRANZ_PLAN_FRESH",
        "云端内容与更新计划",
        PreflightCheckStatus.BLOCKED
        if stale
        else PreflightCheckStatus.WARNING
        if unresolved
        else PreflightCheckStatus.PASSED,
        "翻译内容刚刚发生变化，之前的选择已清除。请重新查看这些翻译后再确认。"
        if stale
        else (
            f"有 {unresolved:,} 条翻译需要你确认，{'本地' if download else '云端'}内容暂时保持不变。"
            f"其他翻译可以先{'更新' if download else '上传'}。"
        )
        if unresolved
        else "",
    )
    checks = (check,)
    if download and bool(getattr(value.ui_context, "uses_authoritative_projection", False)):
        checks += (
            PreflightCheckState(
                "PARATRANZ_LOCAL_SOURCE_SCOPE",
                "当前来源的译文更新",
                PreflightCheckStatus.WARNING,
                "只更新当前来源中的译文和翻译状态。原文和上下文保持原样；云端多出的内容不会下载。",
            ),
        )
    if not download:
        checks += (
            PreflightCheckState(
                "PARATRANZ_REMOTE_SOURCE_PRESERVED",
                "云端来源内容保护",
                PreflightCheckStatus.PASSED,
                "已有云端翻译只更新译文和翻译状态，原文和上下文保持原样；新内容会单独列出。",
            ),
        )
    return prepared, DomainPreflightResult(
        checks,
        download_impact(plan, value.local_entries) if download else upload_impact(plan),
        review_rows(plan),
    )


def _external_error_label(category: str) -> str:
    return {
        "authentication": "账号认证失败",
        "permission": "当前账号没有访问权限",
        "rate_limited": "请求过于频繁",
        "timeout": "连接超时",
        "unavailable": "服务暂时不可用",
        "transport": "网络连接失败",
        "cancelled": "检查已取消",
    }.get(category, "远端服务返回错误")
