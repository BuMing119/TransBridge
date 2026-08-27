"""Embedding 客户端工厂函数测试 — provider 分派与字段映射（P0 断连修复回归）。

覆盖 create_embedding_client 按 config.embedding.* 子对象分派：
- mode=local → LocalSentenceTransformerClient（只加载已安装路径）
- mode=api → OpenAIEmbeddingClient（使用独立 api_key/base_url）
- disabled/未知配置 → 不加载任何后端
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from transbridge.config.llm import EmbeddingConfig, LLMConfig
from transbridge.infra.embedding_client import OpenAIEmbeddingClient, create_embedding_client


def _make_config(
    provider: str = "local",
    *,
    local_model_id: str = "",
    local_model_path: str = "",
    api_key: str = "",
    base_url: str = "",
    model: str = "text-embedding-3-small",
) -> LLMConfig:
    cfg = LLMConfig()
    cfg.embedding = EmbeddingConfig(
        mode="local" if provider == "local" else "api",
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        local_model_id=local_model_id,
        local_model_path=local_model_path,
    )
    cfg.api_key = "llm-key"
    cfg.base_url = "https://llm.example/v1"
    return cfg


class TestCreateEmbeddingClient(unittest.TestCase):
    """create_embedding_client 工厂函数分派逻辑。"""

    @patch("transbridge.infra.embedding_client.LocalSentenceTransformerClient")
    def test_local_provider_without_selected_model_does_not_choose_a_default(self, MockLocal):
        client = create_embedding_client(_make_config(provider="local"))
        MockLocal.assert_called_once_with(model_name="")
        self.assertIs(client, MockLocal.return_value)

    @patch("transbridge.infra.embedding_client.LocalSentenceTransformerClient")
    def test_legacy_local_path_is_not_loaded_without_a_managed_model_id(self, MockLocal):
        create_embedding_client(_make_config(provider="local", local_model_path="/models/minilm"))
        MockLocal.assert_called_once_with(model_name="")

    @patch("transbridge.infra.embedding_client.LocalSentenceTransformerClient")
    def test_managed_local_model_resolves_id_and_passes_versioned_identity(self, MockLocal):
        identity = {
            "model_id": "multilingual-minilm-l12-v2",
            "revision": "fixed-revision",
            "dimension": 384,
        }
        store = Mock()
        store.installed_path.return_value = Path("C:/managed/minilm")
        store.model_identity.return_value = identity
        with patch("transbridge.infra.embedding_model_store.EmbeddingModelStore", return_value=store):
            create_embedding_client(_make_config(provider="local", local_model_id="multilingual-minilm-l12-v2"))

        MockLocal.assert_called_once_with(model_name=str(Path("C:/managed/minilm")), model_identity=identity)

    @patch("transbridge.infra.embedding_client.LocalSentenceTransformerClient")
    def test_invalid_model_catalog_fails_closed_without_crashing_client_factory(self, MockLocal):
        with patch(
            "transbridge.infra.embedding_model_store.EmbeddingModelStore", side_effect=ValueError("bad catalog")
        ):
            create_embedding_client(_make_config(provider="local", local_model_id="managed-model"))

        MockLocal.assert_called_once_with(model_name="")

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
    def test_openai_provider_does_not_fall_back_to_llm_credentials(self, MockOpenAI):
        create_embedding_client(_make_config(provider="openai"))
        MockOpenAI.assert_called_once_with(
            api_key="",
            base_url="",
            model="text-embedding-3-small",
        )

    def test_unknown_provider_does_not_fall_back_to_local(self):
        client = create_embedding_client(_make_config(provider="weird"))

        self.assertFalse(client.available)
        self.assertIn("Unsupported", client.error_message or "")

    def test_disabled_mode_does_not_construct_a_backend(self):
        config = _make_config(provider="openai")
        config.embedding.mode = "disabled"

        with (
            patch("transbridge.infra.embedding_client.LocalSentenceTransformerClient") as local,
            patch("transbridge.infra.embedding_client.OpenAIEmbeddingClient") as api,
        ):
            client = create_embedding_client(config)

        self.assertFalse(client.available)
        local.assert_not_called()
        api.assert_not_called()

    @patch.object(OpenAIEmbeddingClient, "_init_client")
    def test_api_index_identity_excludes_credentials_and_normalizes_endpoint(self, _init_client):
        client = OpenAIEmbeddingClient(
            api_key="embedding-secret",
            model="text-embedding-3-small",
            base_url="HTTPS://User:Password@Example.COM/v1/?token=secret#fragment",
        )

        identity = client.index_identity

        self.assertEqual(identity["base_url"], "https://example.com/v1")
        self.assertNotIn("embedding-secret", json.dumps(identity))
        self.assertNotIn("Password", json.dumps(identity))
        self.assertNotIn("token", json.dumps(identity))


if __name__ == "__main__":
    unittest.main()
