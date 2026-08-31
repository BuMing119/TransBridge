"""Inline user-language help for the current task context."""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from transbridge.ui.shell.action_catalog import IntentId


def _normalise(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


@dataclass(frozen=True, slots=True)
class ContextHelpTopic:
    topic_id: str
    title: str
    purpose: str
    when_to_use: str
    aliases: tuple[str, ...] = ()
    related_intents: tuple[IntentId, ...] = ()

    def __post_init__(self) -> None:
        if not self.topic_id.strip() or not self.title.strip():
            raise ValueError("help topic ID and title must not be empty")
        if not self.purpose.strip() or not self.when_to_use.strip():
            raise ValueError("help topic must explain purpose and when to use it")


@dataclass(frozen=True, slots=True)
class ContextHelpViewState:
    context_identity: str
    topic: ContextHelpTopic


class ContextHelpCatalog:
    def __init__(self, topics: tuple[ContextHelpTopic, ...]) -> None:
        self._topics = topics
        self._by_id = {topic.topic_id: topic for topic in topics}
        if len(self._by_id) != len(topics):
            raise ValueError("context help topic IDs must be unique")

    def get(self, topic_id: str) -> ContextHelpTopic:
        return self._by_id[topic_id]

    def for_intent(self, intent_id: IntentId) -> tuple[ContextHelpTopic, ...]:
        return tuple(topic for topic in self._topics if intent_id in topic.related_intents)

    def search(self, query: str) -> tuple[ContextHelpTopic, ...]:
        value = _normalise(query)
        if not value:
            return self._topics
        matches: list[tuple[int, int, ContextHelpTopic]] = []
        for stable_rank, topic in enumerate(self._topics):
            candidates = (topic.title, *topic.aliases, topic.purpose, topic.when_to_use)
            rank = next(
                (
                    candidate_rank
                    for candidate_rank, candidate in enumerate(candidates)
                    if value in _normalise(candidate)
                ),
                None,
            )
            if rank is not None:
                matches.append((rank, stable_rank, topic))
        matches.sort(key=lambda item: (item[0], item[1]))
        return tuple(item[2] for item in matches)


class ContextHelpController:
    """Owns inline help state only; it has no navigation or task callbacks."""

    def __init__(self, catalog: ContextHelpCatalog) -> None:
        self._catalog = catalog
        self._current: ContextHelpViewState | None = None

    @property
    def current(self) -> ContextHelpViewState | None:
        return self._current

    def show(self, topic_id: str, *, context_identity: str) -> ContextHelpViewState:
        if not context_identity.strip():
            raise ValueError("context identity must not be empty")
        self._current = ContextHelpViewState(context_identity, self._catalog.get(topic_id))
        return self._current

    def search(self, query: str) -> tuple[ContextHelpTopic, ...]:
        return self._catalog.search(query)

    def show_for_intent(self, intent_id: IntentId, *, context_identity: str) -> ContextHelpViewState:
        topics = self._catalog.for_intent(intent_id)
        if not topics:
            raise KeyError(f"no contextual help for intent: {intent_id.value}")
        return self.show(topics[0].topic_id, context_identity=context_identity)

    def close(self) -> None:
        self._current = None


DEFAULT_CONTEXT_HELP = ContextHelpCatalog((
    ContextHelpTopic(
        "local-project",
        "本地翻译工程",
        "在本机保存同一项翻译工作的来源、翻译版本和历史还原点。",
        "需要继续此前工作、切换翻译版本或保存当前翻译时使用。",
        aliases=("工程", "Project"),
        related_intents=(IntentId.PROJECT_OPEN, IntentId.PROJECT_SAVE),
    ),
    ContextHelpTopic(
        "paratranz-project",
        "ParaTranz 云端项目",
        "表示 ParaTranz 服务中的远端协作项目，不等同于本地翻译工程。",
        "准备上传、下载或与团队同步译文时使用。",
        aliases=("云端项目", "同步"),
        related_intents=(IntentId.SYNC_UPLOAD, IntentId.SYNC_DOWNLOAD),
    ),
    ContextHelpTopic(
        "plugin",
        "插件",
        "表示真实的 ESP、ESM 或 ESL 来源文件。",
        "要解析游戏插件、开始翻译或把译文写回插件时使用。",
        aliases=("ESP", "ESM", "ESL"),
        related_intents=(IntentId.PROJECT_CREATE, IntentId.PUBLISH_WRITE),
    ),
    ContextHelpTopic(
        "translation-content",
        "翻译内容",
        "表示当前可编辑的词条集合，也可来自 JSON、EET、XT、SST 等非插件来源。",
        "来源不是插件、来源类型不确定或多个来源合并时使用这个称呼。",
        aliases=("词条", "CollectionSlot"),
        related_intents=(IntentId.WORKBENCH_MANAGE,),
    ),
    ContextHelpTopic(
        "translation-version",
        "翻译版本",
        "表示同一本地翻译工程内可以切换并继续编辑的一份译文状态。",
        "需要保留不同译法、复制当前工作或切换编辑状态时使用。",
        aliases=("版本", "Variant"),
        related_intents=(IntentId.PROJECT_VARIANT_CREATE, IntentId.PROJECT_VARIANT_COPY),
    ),
    ContextHelpTopic(
        "restore-point",
        "历史还原点",
        "表示某个翻译版本可恢复的只读历史状态，不是可直接编辑的翻译版本。",
        "需要在修改前留档，或从过去状态恢复时使用。",
        aliases=("快照", "Snapshot"),
        related_intents=(IntentId.PROJECT_SNAPSHOT_SAVE, IntentId.PROJECT_SNAPSHOT_LOAD),
    ),
    ContextHelpTopic(
        "task",
        "任务",
        "表示有 Run ID、所属对象和可观察终态的一次后台执行。",
        "运行 AI 翻译、同步、写回或 FOMOD，并需要查看进度、日志或结果时使用。",
        aliases=("运行", "Run ID", "任务中心"),
        related_intents=(IntentId.TASK_OPEN_ACTIVITY,),
    ),
    ContextHelpTopic(
        "ai-translation",
        "AI 自动翻译",
        "按当前翻译内容和作用域创建一次独立的 AI 翻译任务。",
        "已经选好要处理的翻译内容，并希望批量生成或润色译文时使用；长期服务配置在高级设置中修改。",
        aliases=("大模型", "LLM", "润色"),
        related_intents=(IntentId.TRANSLATION_AI,),
    ),
    ContextHelpTopic(
        "paratranz-sync",
        "ParaTranz 同步",
        "在本地翻译内容与 ParaTranz 云端项目之间上传或下载译文。",
        "需要和云端协作结果对齐时使用；提交前先检查计划、权限和覆盖影响。",
        aliases=("上传", "下载", "合并"),
        related_intents=(
            IntentId.SYNC_UPLOAD,
            IntentId.SYNC_UPLOAD_BATCH,
            IntentId.SYNC_DOWNLOAD,
            IntentId.SYNC_DOWNLOAD_BATCH,
        ),
    ),
    ContextHelpTopic(
        "write-back",
        "写回文件",
        "把当前译文发布到受支持的插件或输出文件。",
        "完成检查且准备生成正式文件时使用；提交前确认目标、备份和覆盖范围。",
        aliases=("发布", "导出译文"),
        related_intents=(IntentId.PUBLISH_WRITE, IntentId.PUBLISH_WRITE_BATCH),
    ),
    ContextHelpTopic(
        "fomod",
        "FOMOD 安装包翻译",
        "处理 FOMOD 安装器文本并生成可发布的安装包产物。",
        "来源包含 FOMOD 配置，且需要检查归档与发布计划时使用。",
        aliases=("安装器", "安装包"),
        related_intents=(IntentId.PUBLISH_FOMOD,),
    ),
))


__all__ = [
    "ContextHelpCatalog",
    "ContextHelpController",
    "ContextHelpTopic",
    "ContextHelpViewState",
    "DEFAULT_CONTEXT_HELP",
]
