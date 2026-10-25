"""Qt-free explanations of the planner's existing synchronization reasons."""

from transbridge.application.sync.models import SyncOperation

_REASONS = {
    "duplicate_local_key": "本地存在重复的条目标识，无法确定要处理哪一条。",
    "duplicate_remote_key": "同一标识下有多条云端内容，原文和上下文仍不足以唯一对应。",
    "multiple_remote_translations": "相同原文存在不同译文，现有内容不足以唯一确定采用项。",
    "no_matching_original": "当前插件中没有精确匹配原文的云端译文，已保留本地内容。",
    "remote_untranslated": "匹配原文的云端条目尚无译文，已保留本地内容。",
    "download_original_match": "已按当前插件的精确原文采用唯一译文。",
    "download_translation_consensus": "相同原文的多个记录具有相同译文，已按译文内容自动合并。",
    "download_key_match": "已按精确原文和词条信息确定译文。",
    "download_current_translation_match": "已通过本地已有译文确定对应内容。",
    "duplicate_remote_id": "云端快照包含重复的记录编号，无法安全确定目标。",
    "original_match_required": "没有找到能确认原文对应关系的云端条目。",
    "ambiguous_remote_match": "存在多条可能对应的云端内容，无法唯一确定目标。",
    "original_mismatch": "候选云端条目的原文与本地原文不同。",
    "remote_id_missing": "候选云端条目缺少可用的记录编号。",
    "remote_reference_missing": "在本次云端快照中找不到原先关联的记录。",
    "remote_reference_stale": "在本次云端快照中找不到原先关联的记录。",
    "stale_remote_reference": "在本次云端快照中找不到原先关联的记录。",
    "remote_deleted": "候选云端条目已被标记删除。",
    "remote_already_linked": "候选云端记录已被另一本地条目关联。",
    "remote_reference_owned": "候选云端记录已被另一本地条目关联。",
    "remote_reference_collision": "多个本地条目对应同一个云端记录，不能同时处理。",
    "shared_remote_candidate": "多个本地条目会使用同一个云端记录，不能同时处理。",
    "local_key_collision": "多个本地条目会使用同一个云端标识，无法分别新增。",
    "content_changed": "本地与云端内容或关联状态不同，当前同步策略要求先确认。",
}


def explain_sync_reason(code: str, operation: SyncOperation | str) -> str:
    """Explain a reason without guessing a new matching rule or remediation."""
    if code == "shared_remote_candidate":
        return (
            "多个本地条目会上传到同一个云端记录，不能同时覆盖。"
            if operation == SyncOperation.UPLOAD
            else "多个本地条目会关联同一个云端记录，不能同时采用。"
        )
    return _REASONS.get(code, f"同步计划无法安全处理此条目；原因代码：{code}。")
