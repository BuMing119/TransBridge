"""Embedding 客户端工厂函数测试 — provider 分派与字段映射（P0 断连修复回归）。

覆盖 create_embedding_client 按 config.embedding.* 子对象分派：
- provider=local → LocalSentenceTransformerClient（含 local_model_path 映射）
- provider=openai/custom/api → OpenAIEmbeddingClient（api_key/base_url 回退）
- 未知 provider → 回退本地模型
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from transbridge.config.llm import EmbeddingConfig, LLMConfig
from transbridge.infra.embedding_client import create_embedding_client


def _make_config(
    provider: str = "local",
    *,
    local_model_path: str = "",
    api_key: str = "",
    base_url: str = "",
    model: str = "text-embedding-3-small",
) -> LLMConfig:
    cfg = LLMConfig()
    cfg.embedding = EmbeddingConfig(
        mode="api",
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        local_model_path=local_model_path,
    )
    cfg.api_key = "llm-key"
    cfg.base_url = "https://llm.example/v1"
    return cfg


class TestCreateEmbeddingClient(unittest.TestCase):
    """create_embedding_client 工厂函数分派逻辑。"""

    @patch("transbridge.infra.embedding_client.LocalSentenceTransformerClient")
    def test_local_provider_default_model(self, MockLocal):
        client = create_embedding_client(_make_config(provider="local"))
        MockLocal.assert_called_once_with()
        self.assertIs(client, MockLocal.return_value)

    @patch("transbridge.infra.embedding_client.LocalSentenceTransformerClient")
    def test_local_provider_with_path(self, MockLocal):
        create_embedding_client(
            _make_config(provider="local", local_model_path="/models/minilm")
        )
        MockLocal.assert_called_once_with(model_name="/models/minilm")

    @patch("transbridge.infra.embedding_client.OpenAIEmbeddingClient")
    def test_openai_provider_full(self, MockOpenAI):
        client = create_embedding_client(
            _make_config(
                provider="openai",
                api_key="emb-key",
                base_url="https://emb.example/v1",
                model="text-embedding-3-small",
            )
        )
        MockOpenAI.assert_called_once_with(
            api_key="emb-key",
            base_url="https://emb.example/v1",
            model="text-embedding-3-small",
        )
        self.assertIs(client, MockOpenAI.return_value)

    @patch("transbridge.infra.embedding_client.OpenAIEmbeddingClient")
    def test_openai_provider_falls_back_to_llm_key_and_url(self, MockOpenAI):
        # embedding 未配置独立 api_key/base_url 时回退到 LLM 主配置
        create_embedding_client(_make_config(provider="openai"))
        MockOpenAI.assert_called_once_with(
            api_key="llm-key",
            base_url="https://llm.example/v1",
            model="text-embedding-3-small",
        )

    @patch("transbridge.infra.embedding_client.LocalSentenceTransformerClient")
    def test_unknown_provider_fallback_local(self, MockLocal):
        create_embedding_client(_make_config(provider="weird"))
        MockLocal.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
