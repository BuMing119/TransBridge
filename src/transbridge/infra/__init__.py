from typing import TYPE_CHECKING

from .config import LLMConfig
from .embedding_client import EmbeddingClient
from .llm_client import LLMClient, create_llm_client
from .llm_structured_outputs import (
    LlmOutputSchema,
    LlmStructuredOutputError,
    LlmStructuredOutputInvalidResponseError,
    LlmStructuredOutputRefusalError,
    LlmStructuredOutputTruncatedError,
    LlmStructuredOutputUnsupportedError,
    attach_structured_output_directive,
)
from .vector_store import VectorStore

if TYPE_CHECKING:
    from .markdown_renderer import MarkdownRenderer


def __getattr__(name: str):
    if name == "MarkdownRenderer":
        from .markdown_renderer import MarkdownRenderer

        return MarkdownRenderer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "LLMClient",
    "create_llm_client",
    "EmbeddingClient",
    "LLMConfig",
    "VectorStore",
    "MarkdownRenderer",
    "LlmOutputSchema",
    "LlmStructuredOutputError",
    "LlmStructuredOutputInvalidResponseError",
    "LlmStructuredOutputRefusalError",
    "LlmStructuredOutputTruncatedError",
    "LlmStructuredOutputUnsupportedError",
    "attach_structured_output_directive",
]
