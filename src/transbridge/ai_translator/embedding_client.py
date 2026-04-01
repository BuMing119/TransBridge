"""
Embedding 客户端抽象层。

提供：
- EmbeddingClient (ABC)
- LocalSentenceTransformerClient - 本地 sentence-transformers 模型
- OpenAIEmbeddingClient - OpenAI 兼容 API（支持 OpenAI、DeepSeek、阿里云等）
- create_embedding_client() 工厂函数
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from src.transbridge.paratranz.config_manager import LLMConfig

logger = logging.getLogger(__name__)


class EmbeddingClient(ABC):
    """Embedding 客户端抽象基类。"""

    @abstractmethod
    def encode(self, texts: list[str]) -> np.ndarray:
        """
        编码文本列表，返回归一化的 embedding 矩阵。

        Args:
            texts: 待编码的文本列表

        Returns:
            np.ndarray: shape=(len(texts), dimension), dtype=float32, 已归一化
        """

    @property
    @abstractmethod
    def dimension(self) -> int:
        """返回 embedding 向量维度。"""

    @property
    def available(self) -> bool:
        """客户端是否可用。"""
        return True

    @property
    def error_message(self) -> str | None:
        """初始化失败时的错误信息。"""
        return None


class LocalSentenceTransformerClient(EmbeddingClient):
    """
    本地 sentence-transformers 模型实现。

    可选依赖：sentence-transformers。未安装时 available=False。
    """

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        self._model_name = model_name
        self._model = None
        self._dimension = 0
        self._available = False
        self._error_message: str | None = None
        self._load_model()

    def _resolve_model_path(self) -> str:
        """优先使用打包内的本地模型，回退到 HuggingFace 在线下载。"""
        import sys
        if getattr(sys, "frozen", False):
            # PyInstaller onedir：_MEIPASS 指向 EXE 所在目录
            base = sys._MEIPASS
        else:
            # 开发模式：相对于项目根目录
            base = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
        local_path = os.path.join(base, "data", "models", self._model_name)
        return local_path if os.path.isdir(local_path) else self._model_name

    def _load_model(self) -> None:
        """延迟加载模型。"""
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            self._error_message = "sentence-transformers not installed"
            logger.warning(self._error_message)
            return

        model_path = self._resolve_model_path()
        try:
            self._model = SentenceTransformer(model_path)
            # 获取维度（通过编码一个测试字符串）
            test_embedding = self._model.encode(["test"])
            self._dimension = test_embedding.shape[1]
            self._available = True
            logger.info(f"Loaded local embedding model '{model_path}' (dim={self._dimension})")
        except Exception as e:
            self._error_message = f"Failed to load embedding model '{model_path}': {e}"
            logger.error(self._error_message)

    @property
    def available(self) -> bool:
        return self._available

    @property
    def error_message(self) -> str | None:
        return self._error_message

    @property
    def dimension(self) -> int:
        return self._dimension

    def encode(self, texts: list[str]) -> np.ndarray:
        if not self._available or self._model is None:
            raise RuntimeError(f"Embedding client not available: {self._error_message}")

        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.array(embeddings).astype("float32")


class OpenAIEmbeddingClient(EmbeddingClient):
    """
    OpenAI 兼容 Embedding API 实现。

    支持：
    - OpenAI 官方：text-embedding-3-small, text-embedding-3-large, text-embedding-ada-002
    - DeepSeek：通过 base_url 指向 DeepSeek API
    - 阿里云通义：通过 base_url 指向阿里云 API
    - 其他 OpenAI 兼容服务
    """

    # 常用模型的维度
    MODEL_DIMENSIONS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
        "text-embedding-v3": 1024,  # DeepSeek
    }

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._dimension = self.MODEL_DIMENSIONS.get(model, 1536)  # 默认 1536
        self._available = False
        self._error_message: str | None = None
        self._init_client()

    def _init_client(self) -> None:
        """初始化 OpenAI 客户端。"""
        if not self._api_key:
            self._error_message = "API key not configured"
            return

        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
            )
            self._available = True
            logger.info(f"Initialized OpenAI embedding client (model={self._model}, base_url={self._base_url})")
        except ImportError:
            self._error_message = "openai package not installed"
            logger.warning(self._error_message)
        except Exception as e:
            self._error_message = f"Failed to initialize OpenAI client: {e}"
            logger.error(self._error_message)

    @property
    def available(self) -> bool:
        return self._available

    @property
    def error_message(self) -> str | None:
        return self._error_message

    @property
    def dimension(self) -> int:
        return self._dimension

    def encode(self, texts: list[str]) -> np.ndarray:
        if not self._available:
            raise RuntimeError(f"Embedding client not available: {self._error_message}")

        try:
            # OpenAI API 单次请求限制，分批处理
            batch_size = 2048  # OpenAI 推荐的最大 batch size
            all_embeddings = []

            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                response = self._client.embeddings.create(
                    model=self._model,
                    input=batch,
                )
                batch_embeddings = [item.embedding for item in response.data]
                all_embeddings.extend(batch_embeddings)

            embeddings = np.array(all_embeddings).astype("float32")

            # 归一化（OpenAI embedding 不一定归一化）
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)  # 避免除零
            embeddings = embeddings / norms

            # 更新实际维度
            if embeddings.shape[1] != self._dimension:
                self._dimension = embeddings.shape[1]
                logger.debug(f"Updated embedding dimension to {self._dimension}")

            return embeddings

        except Exception as e:
            logger.error(f"OpenAI embedding API error: {e}")
            raise


def create_embedding_client(config: "LLMConfig") -> EmbeddingClient:
    """
    工厂函数，按配置创建 EmbeddingClient 实例。

    优先级：
    1. 如果 embedding_provider 为空或 "local"，使用本地模型
    2. 否则使用指定的 API 服务

    Args:
        config: LLMConfig 实例

    Returns:
        EmbeddingClient 实例（可能 available=False）
    """
    provider = getattr(config, "embedding_provider", "local") or "local"

    if provider == "local":
        model_name = getattr(config, "embedding_local_model", "paraphrase-multilingual-MiniLM-L12-v2")
        return LocalSentenceTransformerClient(model_name=model_name)

    # API 服务（openai / anthropic / custom）
    api_key = getattr(config, "embedding_api_key", "") or config.api_key
    base_url = getattr(config, "embedding_base_url", "") or config.base_url
    model = getattr(config, "embedding_model", "text-embedding-3-small")

    if provider == "openai" or provider == "custom":
        return OpenAIEmbeddingClient(
            api_key=api_key,
            base_url=base_url,
            model=model,
        )

    # 未知 provider，回退到本地模型
    logger.warning(f"Unknown embedding provider '{provider}', falling back to local model")
    return LocalSentenceTransformerClient()
