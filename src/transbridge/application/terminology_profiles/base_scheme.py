"""Create an independent naming draft from the published project terminology."""

from transbridge.application.terminology.effective import EffectiveSnapshotStatus

from .models import ProfileTermMapping, TerminologyProfileContent


def content_from_project_terminology(snapshot, *, project_id: str, variant_id: str) -> TerminologyProfileContent:
    if (snapshot.local_project_id, snapshot.local_variant_id) != (project_id, variant_id):
        raise ValueError("项目或翻译版本已变化，请重新创建方案。")
    if snapshot.status is not EffectiveSnapshotStatus.READY:
        raise ValueError("请先创建并发布项目术语，再基于项目术语创建方案。")
    mappings = tuple(
        ProfileTermMapping(
            item.original, item.translation, item.translation, item.scope.kind.value, item.scope.plugin_id
        )
        for item in snapshot.decisions
        if item.is_effective
    )
    if not mappings:
        raise ValueError("当前项目没有已发布的有效术语，请先添加或整理术语。")
    return TerminologyProfileContent(mappings=mappings)
