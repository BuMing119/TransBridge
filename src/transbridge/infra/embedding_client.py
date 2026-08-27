"""
Embedding 客户端抽象层。

提供：
- EmbeddingClient (ABC)
- LocalSentenceTransformerClient - 本地 sentence-transformers 模型
- OpenAIEmbeddingClient - OpenAI 兼容 API（支持 OpenAI、DeepSeek、阿里云等）
- create_embedding_client() 工厂函数
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import os
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import numpy as np

if TYPE_CHECKING:
    from transbridge.paratranz.config_manager import LLMConfig

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

    @property
    def index_identity(self) -> dict[str, object]:
        """Return a stable, secret-free identity for persisted vector indexes."""

        return {
            "backend": f"{type(self).__module__}.{type(self).__qualname__}",
            "dimension": self.dimension,
        }


class LocalSentenceTransformerClient(EmbeddingClient):
    """
    本地 sentence-transformers 模型实现。

    可选依赖：sentence-transformers。未安装时 available=False。
    """

    def __init__(
        self,
        model_name: str = "",
        models_dir: str | None = None,
        *,
        model_identity: dict[str, object] | None = None,
    ):
        self._model_name = model_name
        self._models_dir = models_dir  # 自定义本地模型目录，None 时使用默认路径
        self._managed_identity = dict(model_identity or {})
        self._model = None
        self._dimension = 0
        self._available = False
        self._error_message: str | None = None
        self._load_model()

    def _resolve_model_path(self) -> str | None:
        """Resolve only an already-installed local model; never trigger a remote download."""

        requested = self._model_name.strip()
        if not requested:
            return None
        candidates = [requested]
        if self._models_dir:
            candidates.append(os.path.join(self._models_dir, requested))
        else:
            # Compatibility for installations created by the legacy implicit downloader.
            from transbridge.config.paths import get_data_dir

            candidates.append(os.path.join(get_data_dir(), "models", requested))
        for candidate in candidates:
            if os.path.isdir(candidate):
                return os.path.abspath(candidate)
        return None

    def _load_model(self) -> None:
        """延迟加载模型。"""
        model_path = self._resolve_model_path()
        if model_path is None:
            self._error_message = "No installed local embedding model is selected"
            logger.info(self._error_message)
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            self._error_message = "sentence-transformers not installed"
            logger.warning(self._error_message)
            return

        try:
            self._model = SentenceTransformer(model_path, local_files_only=True)
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

    @property
    def index_identity(self) -> dict[str, object]:
        identity = {
            "backend": "sentence-transformers",
            "dimension": self.dimension,
            "mode": "local",
            "model": os.path.normcase(os.path.abspath(self._model_name)) if self._model_name else "",
            "models_dir": os.path.normcase(os.path.abspath(self._models_dir)) if self._models_dir else "",
        }
        identity.update(self._managed_identity)
        return identity

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
        self._dimension = self.MODEL_DIMENSIONS.get(model, 0)
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

    @property
    def index_identity(self) -> dict[str, object]:
        return {
            "backend": "openai-compatible",
            "base_url": _normalize_base_url_for_identity(self._base_url),
            "dimension": self.MODEL_DIMENSIONS.get(self._model, "dynamic"),
            "mode": "api",
            "model": self._model,
        }

    def encode(self, texts: list[str]) -> np.ndarray:
        if not self._available:
            raise RuntimeError(f"Embedding client not available: {self._error_message}")

        try:
            # OpenAI API 单次请求限制，分批处理
            batch_size = 2048  # OpenAI 推荐的最大 batch size
            all_embeddings = []

            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                response = self._client.embeddings.create(
                    model=self._model,
                    input=batch,
                )
                batch_embeddings = [item.embedding for item in response.data]
                all_embeddings.extend(batch_embeddings)

            embeddings = np.array(all_embeddings).astype("float32")
            if embeddings.ndim == 2 and embeddings.shape[1] > 0:
                self._dimension = int(embeddings.shape[1])

            # 归一化（OpenAI embedding 不一定归一化）
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)  # 避免除零
            embeddings = embeddings / norms

            return embeddings

        except Exception as e:
            logger.error(f"OpenAI embedding API error: {e}")
            raise


def create_embedding_client(config: LLMConfig) -> EmbeddingClient:
    """
    工厂函数，按配置创建 EmbeddingClient 实例。

    优先级：
    1. embedding.provider 为 "local" 时使用本地模型
    2. 否则使用指定的 API 服务（openai / custom）

    Args:
        config: LLMConfig 实例

    Returns:
        EmbeddingClient 实例（可能 available=False）
    """
    emb = config.embedding  # EmbeddingConfig 子对象
    mode = str(getattr(emb, "mode", "disabled") or "disabled").strip().casefold()
    provider = str(getattr(emb, "provider", "") or "").strip().casefold()

    if mode == "local":
        local_path = ""
        managed_identity = None
        model_id = str(getattr(emb, "local_model_id", "") or "").strip()
        if model_id:
            from transbridge.infra.embedding_model_store import EmbeddingModelStore

            try:
                store = EmbeddingModelStore()
                installed_path = store.installed_path(model_id)
                managed_identity = store.model_identity(model_id) if installed_path is not None else None
            except (KeyError, OSError, ValueError):
                installed_path = None
            if installed_path is not None:
                local_path = str(installed_path)
        if managed_identity is not None:
            return LocalSentenceTransformerClient(model_name=local_path, model_identity=managed_identity)
        return LocalSentenceTransformerClient(model_name=local_path)

    if mode == "api" and provider in ("openai", "custom", "api"):
        return OpenAIEmbeddingClient(
            api_key=emb.api_key,
            base_url=emb.base_url,
            model=emb.model,
        )

    reason = (
        "Embedding is disabled" if mode == "disabled" else f"Unsupported embedding mode/provider: {mode}/{provider}"
    )
    return _UnavailableEmbeddingClient(reason)


class _UnavailableEmbeddingClient(EmbeddingClient):
    """Non-loading client used for disabled or invalid configurations."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def encode(self, texts: list[str]) -> np.ndarray:
        del texts
        raise RuntimeError(self._reason)

    @property
    def dimension(self) -> int:
        return 0

    @property
    def available(self) -> bool:
        return False

    @property
    def error_message(self) -> str | None:
        return self._reason


def _normalize_base_url_for_identity(value: str) -> str:
    """Normalize an endpoint without retaining credentials, query strings, or fragments."""

    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.hostname:
        return raw.rstrip("/")
    host = parsed.hostname.casefold()
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme.casefold(), host, parsed.path.rstrip("/"), "", ""))
