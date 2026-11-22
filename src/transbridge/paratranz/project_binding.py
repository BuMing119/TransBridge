# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Capture the local project and connection used to choose a ParaTranz binding."""

from contextlib import closing
from copy import copy
from dataclasses import dataclass, field
from datetime import datetime

from transbridge.application.ports.paratranz import ParaTranzProject
from transbridge.application.projects import ParaTranzProjectBinding

from .project_catalog import ParaTranzCatalogKey
from .service import ParaTranzService


@dataclass(frozen=True, slots=True)
class BindingSelection:
    local_project_id: str
    local_revision: int | None
    open_generation: int | None
    catalog_key: ParaTranzCatalogKey
    config: object = field(repr=False)
    request_config: object = field(repr=False, compare=False)

    @classmethod
    def capture(cls, context):
        project_id = getattr(context, "active_project_id", None)
        if (
            not project_id
            or getattr(context, "project_opening", False)
            or getattr(context, "project_view_failed", False)
        ):
            raise ValueError("请先打开可用的本地工程，再选择 ParaTranz 同步目标。")
        config = context.config
        user = getattr(context, "current_user", None) or {}
        account_id = user.get("id")
        if account_id is None:
            account_id = getattr(config, "user_id", None)
        elif getattr(config, "user_id", None) is not None and account_id != config.user_id:
            raise ValueError("ParaTranz 当前账号和连接账号不一致，请重新验证连接。")
        key = ParaTranzCatalogKey(config.base_url, account_id, getattr(config, "config_revision", 0))
        snapshot = config.copy_for_execution() if hasattr(config, "copy_for_execution") else copy(config)
        return cls(
            project_id,
            getattr(context, "project_revision", None),
            getattr(context, "project_open_generation", None),
            key,
            config,
            snapshot,
        )

    def member_project(self, catalog, project_id, *, cancellation=None):
        with closing(ParaTranzService.from_config(self.request_config)) as service:
            return catalog.find_member_project(service, self.catalog_key, project_id, cancellation=cancellation)

    def bind(self, context, member_project: ParaTranzProject, *, capture_commit=None):
        current = self.capture(context)
        if (
            self.local_project_id != current.local_project_id
            or self.local_revision != current.local_revision
            or self.open_generation != current.open_generation
        ):
            raise ValueError("当前本地工程在选择期间发生变化，请重新选择同步目标。")
        if self.config is not current.config or self.catalog_key != current.catalog_key:
            raise ValueError("ParaTranz 账号或连接配置已变化，请重新选择同步目标。")
        now = datetime.now().astimezone().isoformat()
        binding = ParaTranzProjectBinding(
            member_project.project_id,
            member_project.name,
            self.catalog_key.endpoint,
            self.catalog_key.account_user_id,
            now,
            now,
        )
        if capture_commit is not None:
            return capture_commit(lambda: context.set_paratranz_binding(binding))
        return context.set_paratranz_binding(binding)
