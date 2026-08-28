"""infra/ 配置 —— 向后兼容层。实际实现已迁移至 src/transbridge/config/。"""

from transbridge.config.llm import EmbeddingConfig, LLMConfig  # noqa: F401

__all__ = ["LLMConfig", "EmbeddingConfig"]
