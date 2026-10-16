"""User-facing terminology synchronization labels and content comparisons."""

from __future__ import annotations

from transbridge.application.terminology_sync.plan_models import TerminologySyncAction

ACTION_LABELS = {
    "create_remote": "新增远端术语",
    "update_remote": "更新远端术语",
    "delete_remote": "删除远端术语",
    "propose_local_add": "建议新增本地术语",
    "propose_local_update": "建议更新本地术语",
    "propose_local_suppression": "建议停用本地术语",
    "adopt_link": "关联已有远端术语",
    "skip": "无需变更",
    "lossy_mapping": "存在无法同步的属性",
    "conflict": "需要解决冲突",
    "blocked": "无法执行",
}
STATUS_LABELS = {
    "succeeded": "成功",
    "failed": "失败",
    "skipped": "跳过",
    "unknown": "远端结果未知",
    "cancelled": "已停止",
    "reconciled": "已核对",
    "queued": "等待执行",
    "running": "执行中",
    "paused": "已暂停",
    "cancelling": "正在停止",
    "completed": "已完成",
}
REASON_LABELS = {
    "local_only": "仅本地存在",
    "independent_remote": "仅远端存在",
    "unchanged_echo": "两端内容未改变",
    "safe_match_proposal": "两端内容一致，可建立关联",
    "local_changed": "本地内容已改变",
    "remote_changed": "远端内容已改变",
    "both_changed": "两端均有修改，需要复核",
    "local_deleted": "本地术语已停用或删除",
    "remote_deleted": "远端术语已删除",
    "both_deleted": "两端均已删除",
    "unknown_outcome": "上次远端结果未知，先核对再继续",
    "remote_id_missing": "关联的远端术语已不存在",
    "remote_id_reused": "远端编号对应的内容已改变",
    "duplicate_local_identity": "本地存在重复术语",
    "duplicate_remote_identity": "远端存在重复术语",
    "plugin_scope": "插件作用域无法直接同步",
    "suppression_not_representable": "远端不支持本地停用状态",
    "replacement_not_representable": "远端不支持本地替换关系",
    "variant_mapping_conflict": "目标已关联另一翻译版本",
    "remote_snapshot_unstable": "读取期间远端发生变化，请重新生成计划",
    "baseline_unavailable": "缺少共同基线",
}
INBOUND_LABELS = {
    "remote_add": "远端新增",
    "remote_update": "远端修改",
    "remote_delete": "远端删除 → 本地停用",
    "remote_conflict": "两端冲突",
}
EFFECT_LABELS = {
    "add": "新增到草稿",
    "update": "更新草稿",
    "suppress": "停用术语",
    "reject": "拒绝变化",
    "conflict": "阻断，需处理",
    "no_change": "无需修改",
}
_IMPORT_DIAGNOSTICS = {
    "remote conflict requires edit or rejection": "两端均已修改：请选择“编辑”确认译名，或“拒绝”保留本地内容。",
    "local term for remote deletion no longer exists": "本地术语已不存在：拒绝此项，或重新同步后复核。",
    "local term changed after remote deletion was planned": "本地术语已改变：拒绝删除，或重新同步后复核。",
    "inbound item has no committable terminology content": "此项没有可导入内容，请拒绝或重新同步。",
    "remote attributes are not representable in local decisions": (
        "远端大小写或词性属性无法导入：请拒绝此项并调整远端属性后重新同步。"
    ),
    "local term changed after inbound update was planned": (
        "本地术语已改变：请选择“编辑”确认译名，或“拒绝”保留本地内容。"
    ),
    "normalized original conflicts with an existing draft term": "草稿中已有同名术语：请拒绝此项并在草稿中解决重名。",
}


def label(value, labels) -> str:
    key = str(getattr(value, "value", value))
    return labels.get(key, key)


def import_diagnostic(message: str | None) -> str:
    return _IMPORT_DIAGNOSTICS.get(message, message or "")


def content_text(content) -> str:
    if content is None:
        return "—"
    scope = getattr(content, "scope", "project")
    scope = getattr(scope, "canonical_key", scope)
    values = [f"{content.original} → {content.translation or '（无译名）'}"]
    if getattr(content, "suppressed", False):
        values.append("停用")
    if str(scope).startswith("plugin:"):
        values.append(f"插件：{str(scope)[7:]}")
    else:
        values.append("范围：当前项目")
    if getattr(content, "variants", ()):
        values.append("别名：" + "、".join(content.variants))
    note = getattr(content, "note", getattr(content, "notes", ""))
    if note:
        values.append("备注：" + note)
    if getattr(content, "case_sensitive", False):
        values.append("区分大小写")
    if getattr(content, "part_of_speech", ""):
        values.append("词性：" + content.part_of_speech)
    if str(getattr(content, "status", "")) == "review_required":
        values.append("待复核，尚不能发布")
    return "\n".join(values)


def plan_comparison(item) -> tuple[str, str]:
    if item.action is TerminologySyncAction.DELETE_REMOTE:
        return content_text(item.remote), "删除远端术语；无法自动撤销"
    if item.action in {TerminologySyncAction.CREATE_REMOTE, TerminologySyncAction.UPDATE_REMOTE}:
        return content_text(item.remote), content_text(item.local)
    if item.action is TerminologySyncAction.PROPOSE_LOCAL_SUPPRESSION:
        return content_text(item.local), "导入草稿后停用；发布后生效"
    if item.action in {TerminologySyncAction.PROPOSE_LOCAL_ADD, TerminologySyncAction.PROPOSE_LOCAL_UPDATE}:
        return content_text(item.local), content_text(item.remote)
    return content_text(item.local), content_text(item.remote)


def target_text(target, fallback: str = "") -> str:
    if target is None:
        return "同步目标：" + (fallback or "尚未选择")
    return f"服务：{target.endpoint} · 远端项目：{target.remote_project_id} · 账号：{target.account_user_id}"
