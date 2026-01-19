from datetime import datetime
from enum import Enum, StrEnum, auto
from typing import Any, Literal

from pydantic import BaseModel, Field


class ProcessingState(Enum):
    """流水线处理状态"""

    INITIAL = auto()  # 初始状态
    PREPROCESSING = auto()  # 预处理中
    TRANSLATING = auto()  # 翻译中
    REVIEWING = auto()  # 审阅中
    COMPLETED = auto()  # 完成
    ERROR = auto()  # 错误


class MatchState(Enum):
    """数据库匹配状态"""

    NO_MATCH = auto()  # 无匹配
    PARTIAL_MATCH = auto()  # 半匹配 (需人工/策略确认)
    FULL_MATCH = auto()  # 全匹配 (待确认)


class ValidationState(Enum):
    """验证状态"""

    UNCHECKED = auto()  # 未检查
    PASSED = auto()  # 通过
    FAILED = auto()  # 失败


class ReviewState(Enum):
    """审阅状态"""

    NOT_REVIEWED = auto()  # 未审阅
    PENDING_REVIEW = auto()  # 待审阅 (验证通过)
    REVIEWED_UNCHANGED = auto()  # 已审阅 (无修改)
    REVIEWED_MODIFIED = auto()  # 已审阅 (有修改)
    NEEDS_MANUAL_REVIEW = auto()  # 需人工复核 (专家模式兜底)


class StateVector(BaseModel):
    """状态向量"""

    processing: ProcessingState = Field(default=ProcessingState.INITIAL, description="流水线进度")
    match: MatchState = Field(default=MatchState.NO_MATCH, description="DB匹配程度")
    validation: ValidationState = Field(default=ValidationState.UNCHECKED, description="验证结果")
    review: ReviewState = Field(default=ReviewState.NOT_REVIEWED, description="审阅结果")


class ComplexityScore(BaseModel):
    """复杂度评分"""

    total_score: float = Field(..., ge=0.0, le=100.0, description="综合评分 (0-100)")
    details: dict[str, float] = Field(
        ...,
        description="分项评分: type_score, text_feature_score, context_missing_score",
    )


class ContextType(StrEnum):
    """上下文类型"""

    NPC = "NPC"
    INFO = "INFO"
    DIAL = "DIAL"
    GENERIC = "GENERIC"


class BaseContext(BaseModel):
    """上下文基类"""

    type: ContextType = Field(..., description="上下文类型")
    related_items: list[str] = Field(default_factory=list, description="关联条目ID列表")
    user_note: str | None = Field(None, description="用户备注")
    segmentation_data: dict[str, Any] | None = Field(None, description="分割后的元素结构，用于还原")
    extra_data: dict[str, Any] = Field(default_factory=dict, description="扩展数据")


class GenericContext(BaseContext):
    """通用上下文"""

    type: Literal[ContextType.GENERIC] = ContextType.GENERIC


class NPCContext(BaseContext):
    """NPC记录上下文"""

    type: Literal[ContextType.NPC] = ContextType.NPC
    npc_sex: str | None = Field(None, description="NPC性别")
    npc_race: str | None = Field(None, description="NPC种族 (FormID)")
    npc_class: str | None = Field(None, description="NPC职业 (FormID)")


class InfoContext(BaseContext):
    """对话INFO记录上下文"""

    type: Literal[ContextType.INFO] = ContextType.INFO
    quest: str | None = Field(None, description="关联任务 (FormID)")
    dialogue_topic: str | None = Field(None, description="所属对话主题 (DIAL FormID)")
    speaker: str | None = Field(None, description="发言NPC (FormID)")
    emotion: str | None = Field(None, description="情绪类型 (Enum/ID)")
    response_note: str | None = Field(None, description="响应备注 (NAM2)")


class DialContext(BaseContext):
    """对话主题DIAL记录上下文"""

    type: Literal[ContextType.DIAL] = ContextType.DIAL
    quest: str | None = Field(None, description="关联任务 (FormID)")
    dialogue_branch: str | None = Field(None, description="所属对话分支 (DLBR FormID)")


# 上下文联合类型
ContextUnion = NPCContext | InfoContext | DialContext | GenericContext


class TranslationMetadata(BaseModel):
    """元数据"""

    source: str = Field(..., description="导入来源 (插件名/文件路径)")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="最后修改时间")
    original_hash: str = Field(..., description="原文哈希 (用于变更检测)")
    original_raw: str | None = Field(None, description="未清洗的原始字符串 (如果与原文不同)")
    translation_engine: str | None = Field(None, description="翻译引擎 (模型名)")
    user_tags: list[str] = Field(default_factory=list, description="用户标签")
    retry_count: int = Field(default=0, description="自动重试次数")
    reviewer: str | None = Field(None, description="审阅者 (User/Agent Name)")
    cost_estimated: float = Field(default=0.0, description="预估成本 (USD)")
    cost_actual: float = Field(default=0.0, description="实际成本 (USD)")


class TranslationHistory(BaseModel):
    """翻译历史版本"""

    timestamp: datetime = Field(default_factory=datetime.now, description="版本时间")
    translation: str = Field(..., description="译文内容")
    editor: str = Field(..., description="修改者 (User/Agent)")


class TranslationItem(BaseModel):
    """翻译条目数据结构"""

    # 基础信息
    id: str = Field(..., description="唯一标识符")
    form_id: str = Field(..., description="游戏内 FormID")
    editor_id: str = Field(..., description="编辑器 ID")
    index: int | None = Field(None, description="记录内部索引")
    record_type: str = Field(..., description="记录类型 (如 INFO, BOOK)")
    field_name: str = Field(..., description="字段名 (如 NAM1, DESC)")
    source_lang: str = Field(default="en", description="源语言")
    target_lang: str = Field(default="zh", description="目标语言")

    # 内容
    original: str = Field(..., description="原文")
    translation: str | None = Field(None, description="译文")
    history: list[TranslationHistory] = Field(default_factory=list, description="历史版本")

    # 核心状态与评分
    state: StateVector = Field(default_factory=StateVector, description="状态向量")
    complexity: ComplexityScore | None = Field(None, description="复杂度评分")
    is_locked: bool = Field(default=False, description="是否锁定 (防止覆盖)")

    # 辅助信息
    glossary_matches: list[str] = Field(default_factory=list, description="匹配到的术语")

    # 上下文与元数据
    context: ContextUnion | None = Field(None, description="上下文信息", discriminator="type")
    metadata: TranslationMetadata = Field(..., description="元数据")