# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Resolve requested naming schemes in the export worker without changing selection."""

from dataclasses import dataclass, replace

from .models import ProfileState
from .write_projection import FrozenProfileWriteProjection


@dataclass(frozen=True)
class ProfileExportSource:
    choices: tuple[tuple[str, str], ...]
    service: object
    baseline: FrozenProfileWriteProjection

    def resolve(self, selected):
        allowed = {key for key, _label in self.choices}
        if not selected or len(set(selected)) != len(selected) or not set(selected) <= allowed:
            raise ValueError("请选择有效的术语方案。")
        output = []
        for profile_id in selected:
            profile = self.service.get_profile(profile_id)
            if profile.project_id != self.baseline.project_id or profile.state is ProfileState.ARCHIVED:
                raise ValueError("术语方案已归档或不属于当前工程，请重新打开导出窗口。")
            revision = profile.latest_published_revision
            published = None if revision is None else self.service.published_revision(profile_id, revision)
            if published is None:
                raise ValueError(f"方案“{profile.name}”尚无可导出的已发布版本。")
            output.append(replace(self.baseline, profile=published))
        return tuple(output)
