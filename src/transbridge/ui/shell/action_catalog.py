"""Stable user-intent metadata shared by shell action surfaces.

The catalog describes discoverability and presentation only.  It deliberately
does not own callbacks, enabled state, or application commands.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class ActionSection(StrEnum):
    FILE = "file"
    PROJECT = "project"
    TRANSLATION = "translation"
    SYNC_PUBLISH = "sync-publish"
    VIEW = "view"
    SETTINGS = "settings"
    HELP = "help"


class IntentPlacement(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    CONTEXTUAL = "contextual"


class DangerLevel(StrEnum):
    SAFE = "safe"
    CAUTION = "caution"
    DESTRUCTIVE = "destructive"


class IntentId(StrEnum):
    PROJECT_CREATE = "project.create"
    PROJECT_OPEN = "project.open"
    PROJECT_SAVE = "project.save"
    PROJECT_REFRESH = "project.refresh"
    PROJECT_RENAME = "project.rename"
    PROJECT_DELETE = "project.delete"
    PROJECT_VARIANT_CREATE = "project.variant.create"
    PROJECT_VARIANT_COPY = "project.variant.copy"
    PROJECT_SNAPSHOT_SAVE = "project.snapshot.save"
    PROJECT_SNAPSHOT_LOAD = "project.snapshot.load"
    PROJECT_SNAPSHOT_DELETE = "project.snapshot.delete"
    PROJECT_EXPORT = "project.export-transbridge"
    PROJECT_IMPORT = "project.import-transbridge"
    SOURCE_MIGRATE = "source.apply-migration"
    TRANSLATION_AI = "translation.ai-run"
    TRANSLATION_AI_BATCH = "translation.ai-batch"
    TRANSLATION_DICTIONARY = "translation.dictionary"
    TRANSLATION_REVIEW = "translation.review"
    TERMINOLOGY_WORKBENCH = "terminology.workbench"
    WORKBENCH_MANAGE = "workbench.manage"
    WORKBENCH_CONTENT_PREPARE = "workbench.content.prepare"
    SYNC_UPLOAD = "sync.upload"
    SYNC_UPLOAD_BATCH = "sync.upload-batch"
    SYNC_DOWNLOAD = "sync.download"
    SYNC_DOWNLOAD_BATCH = "sync.download-batch"
    PUBLISH_WRITE = "publish.write"
    PUBLISH_WRITE_BATCH = "publish.write-batch"
    PUBLISH_FOMOD = "publish.fomod"
    VIEW_SMART_ASSISTANT = "view.smart-assistant"
    SETTINGS_APPEARANCE = "settings.appearance"
    SETTINGS_SERVICES = "settings.services"
    SETTINGS_ACCOUNT = "settings.account"
    SETTINGS_MESSAGES = "settings.messages"
    HELP_CONTEXT = "help.context"
    HELP_ABOUT = "help.about"
    APP_EXIT = "app.exit"
    TASK_OPEN_ACTIVITY = "task.open-activity"
    TASK_RETRY = "task.retry"

    # Public vocabulary aliases used by state-driven guidance.  Aliases are
    # the same enum objects, so every surface reaches one catalog intent.
    TRANSLATION_IMPORT_SOURCE = "source.apply-migration"
    TRANSLATION_AI_RUN = "translation.ai-run"
    SYNC_PARATRANZ_UPLOAD = "sync.upload"


@dataclass(frozen=True, slots=True)
class ActionDescriptor:
    intent_id: IntentId
    label: str
    section: ActionSection
    placement: IntentPlacement = IntentPlacement.SECONDARY
    danger: DangerLevel = DangerLevel.SAFE
    shortcut: str | None = None
    checkable: bool = False
    aliases: tuple[str, ...] = ()
    status_tip: str = ""

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("action label must not be empty")
        if any(not alias.strip() for alias in self.aliases):
            raise ValueError("action aliases must not contain empty values")


@dataclass(frozen=True, slots=True)
class ActionAvailability:
    """One presentation state; the reason is mandatory while disabled."""

    descriptor: ActionDescriptor
    enabled: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.enabled and self.reason is not None:
            raise ValueError("enabled action cannot carry a disabled reason")
        if not self.enabled and not (self.reason and self.reason.strip()):
            raise ValueError("disabled action requires a user-facing reason")


class ActionCatalog:
    def __init__(self, descriptors: Iterable[ActionDescriptor]) -> None:
        values = tuple(descriptors)
        by_id = {item.intent_id: item for item in values}
        if len(by_id) != len(values):
            raise ValueError("action intent IDs must be unique")
        shortcuts = [item.shortcut.casefold() for item in values if item.shortcut]
        if len(set(shortcuts)) != len(shortcuts):
            raise ValueError("action shortcuts must be unique")
        self._values = values
        self._by_id: Mapping[IntentId, ActionDescriptor] = MappingProxyType(by_id)

    def get(self, intent_id: IntentId) -> ActionDescriptor:
        return self._by_id[intent_id]

    def all(self) -> tuple[ActionDescriptor, ...]:
        return self._values

    def in_section(self, section: ActionSection) -> tuple[ActionDescriptor, ...]:
        return tuple(item for item in self._values if item.section is section)

    def availability(
        self,
        intent_id: IntentId,
        *,
        enabled: bool,
        reason: str | None = None,
    ) -> ActionAvailability:
        return ActionAvailability(self.get(intent_id), enabled, reason)


DEFAULT_ACTION_CATALOG = ActionCatalog((
    ActionDescriptor(
        IntentId.PROJECT_CREATE,
        "新建本地翻译工程…",
        ActionSection.PROJECT,
        aliases=("选择插件", "创建插件工程", "ESP", "ESM", "ESL"),
    ),
    ActionDescriptor(IntentId.PROJECT_OPEN, "打开本地翻译工程…", ActionSection.PROJECT),
    ActionDescriptor(IntentId.PROJECT_SAVE, "保存当前工程", ActionSection.PROJECT, shortcut="Ctrl+S"),
    ActionDescriptor(IntentId.PROJECT_RENAME, "重命名当前工程…", ActionSection.PROJECT),
    ActionDescriptor(
        IntentId.PROJECT_DELETE,
        "删除本地工程…",
        ActionSection.PROJECT,
        danger=DangerLevel.DESTRUCTIVE,
    ),
    ActionDescriptor(
        IntentId.PROJECT_REFRESH,
        "刷新工程列表",
        ActionSection.PROJECT,
        placement=IntentPlacement.CONTEXTUAL,
        shortcut="Ctrl+R",
    ),
    ActionDescriptor(IntentId.PROJECT_VARIANT_CREATE, "新建翻译版本…", ActionSection.PROJECT),
    ActionDescriptor(IntentId.PROJECT_VARIANT_COPY, "复制当前翻译版本…", ActionSection.PROJECT),
    ActionDescriptor(IntentId.PROJECT_SNAPSHOT_SAVE, "创建历史还原点…", ActionSection.PROJECT),
    ActionDescriptor(
        IntentId.PROJECT_SNAPSHOT_LOAD,
        "载入历史还原点…",
        ActionSection.PROJECT,
        danger=DangerLevel.CAUTION,
    ),
    ActionDescriptor(
        IntentId.PROJECT_SNAPSHOT_DELETE,
        "删除历史还原点…",
        ActionSection.PROJECT,
        danger=DangerLevel.DESTRUCTIVE,
    ),
    ActionDescriptor(IntentId.PROJECT_EXPORT, "导出 .transbridge…", ActionSection.FILE),
    ActionDescriptor(IntentId.PROJECT_IMPORT, "导入 .transbridge…", ActionSection.FILE, danger=DangerLevel.CAUTION),
    ActionDescriptor(
        IntentId.SOURCE_MIGRATE,
        "导入已有译文…",
        ActionSection.TRANSLATION,
        aliases=("迁移源", "EET", "XT", "SST", "Strings"),
    ),
    ActionDescriptor(
        IntentId.TRANSLATION_AI,
        "AI 翻译…",
        ActionSection.TRANSLATION,
        placement=IntentPlacement.PRIMARY,
        aliases=("批量翻译插件", "批量翻译 ESP", "ESP AI 翻译"),
        status_tip="选择一个或多个翻译内容，使用统一的 AI 翻译任务",
    ),
    ActionDescriptor(
        IntentId.TRANSLATION_DICTIONARY,
        "翻译词典…",
        ActionSection.TRANSLATION,
        aliases=("术语", "词库"),
    ),
    ActionDescriptor(
        IntentId.TRANSLATION_REVIEW,
        "检查翻译问题",
        ActionSection.TRANSLATION,
        placement=IntentPlacement.CONTEXTUAL,
    ),
    ActionDescriptor(
        IntentId.TERMINOLOGY_WORKBENCH,
        "构建术语库…",
        ActionSection.TRANSLATION,
        placement=IntentPlacement.PRIMARY,
        aliases=("术语工作台", "检查异译", "术语历史", "术语报告"),
        status_tip="构建、检查、调整并发布当前工程的项目术语库",
    ),
    ActionDescriptor(
        IntentId.WORKBENCH_MANAGE,
        "管理当前翻译内容",
        ActionSection.PROJECT,
        placement=IntentPlacement.CONTEXTUAL,
        danger=DangerLevel.CAUTION,
    ),
    ActionDescriptor(
        IntentId.WORKBENCH_CONTENT_PREPARE,
        "为当前工程添加插件…",
        ActionSection.TRANSLATION,
        placement=IntentPlacement.CONTEXTUAL,
        aliases=("当前工程加载插件", "添加新插件"),
    ),
    ActionDescriptor(
        IntentId.SYNC_UPLOAD,
        "上传当前内容至 ParaTranz…",
        ActionSection.SYNC_PUBLISH,
        danger=DangerLevel.CAUTION,
    ),
    ActionDescriptor(
        IntentId.SYNC_UPLOAD_BATCH,
        "批量上传至 ParaTranz…",
        ActionSection.SYNC_PUBLISH,
        danger=DangerLevel.CAUTION,
    ),
    ActionDescriptor(
        IntentId.SYNC_DOWNLOAD,
        "从 ParaTranz 下载并合并…",
        ActionSection.SYNC_PUBLISH,
        danger=DangerLevel.CAUTION,
    ),
    ActionDescriptor(
        IntentId.SYNC_DOWNLOAD_BATCH,
        "批量下载并合并…",
        ActionSection.SYNC_PUBLISH,
        danger=DangerLevel.CAUTION,
    ),
    ActionDescriptor(
        IntentId.PUBLISH_WRITE,
        "写回当前文件…",
        ActionSection.SYNC_PUBLISH,
        placement=IntentPlacement.PRIMARY,
        danger=DangerLevel.CAUTION,
    ),
    ActionDescriptor(
        IntentId.PUBLISH_WRITE_BATCH,
        "批量写回文件…",
        ActionSection.SYNC_PUBLISH,
        danger=DangerLevel.CAUTION,
    ),
    ActionDescriptor(
        IntentId.PUBLISH_FOMOD,
        "FOMOD 安装包翻译…",
        ActionSection.SYNC_PUBLISH,
        danger=DangerLevel.CAUTION,
    ),
    ActionDescriptor(
        IntentId.VIEW_SMART_ASSISTANT,
        "智能助手面板",
        ActionSection.VIEW,
        shortcut="Ctrl+Shift+I",
        checkable=True,
        aliases=("聊天", "助手"),
    ),
    ActionDescriptor(
        IntentId.SETTINGS_APPEARANCE,
        "设置…",
        ActionSection.SETTINGS,
        aliases=("通用设置", "主题", "浅色", "深色", "语言", "无障碍", "AI 设置", "Embedding 设置", "ParaTranz 设置"),
        status_tip="管理外观、AI 服务、Embedding、ParaTranz 与默认参数",
    ),
    ActionDescriptor(
        IntentId.SETTINGS_SERVICES,
        "服务与 API 配置…",
        ActionSection.SETTINGS,
        status_tip="配置 ParaTranz 与 AI 服务连接",
    ),
    ActionDescriptor(IntentId.SETTINGS_ACCOUNT, "ParaTranz 账户信息", ActionSection.SETTINGS),
    ActionDescriptor(IntentId.SETTINGS_MESSAGES, "ParaTranz 私信", ActionSection.SETTINGS),
    ActionDescriptor(IntentId.HELP_CONTEXT, "功能与术语帮助", ActionSection.HELP),
    ActionDescriptor(IntentId.HELP_ABOUT, "关于 TransBridge", ActionSection.HELP),
    ActionDescriptor(IntentId.APP_EXIT, "退出", ActionSection.FILE, shortcut="Ctrl+Q"),
    ActionDescriptor(
        IntentId.TASK_OPEN_ACTIVITY,
        "查看任务与结果",
        ActionSection.VIEW,
        placement=IntentPlacement.CONTEXTUAL,
    ),
    ActionDescriptor(
        IntentId.TASK_RETRY,
        "重新检查并重试任务",
        ActionSection.VIEW,
        placement=IntentPlacement.CONTEXTUAL,
        danger=DangerLevel.CAUTION,
    ),
))


__all__ = [
    "ActionAvailability",
    "ActionCatalog",
    "ActionDescriptor",
    "ActionSection",
    "DEFAULT_ACTION_CATALOG",
    "DangerLevel",
    "IntentId",
    "IntentPlacement",
]
