"""OpenAICompatibleClient / AnthropicClient 的 Provider 缓存集成测试。

覆盖（Story 15 翻译层 + Story 14 后处理共享的 Prompt Cache 集成）：
- 官方 base_url + 显式断点模型：system 转为带 prompt_cache_breakpoint 的 content blocks，
  request_options 走 extra_body（含 prompt_cache_options / prompt_cache_key）。
- 自动前缀模型：干净标准消息 + prompt_cache_key 经 extra_body 传递；无内部元数据泄漏。
- 非官方兼容端点：只收到清理后的标准消息，无任何私有字段。
- 普通 chat 与 chat_stream 共用同一转换器（参数形态一致）。
- 缓存参数被 Provider 拒绝（401/400 含 cache）时，仅去掉缓存参数重试一次。
- Anthropic：内部指令 system 转为带 ephemeral cache_control 的 content blocks + user_messages。

用 mock 的 OpenAI / Anthropic client 提供服务，不真实联网。
"""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from transbridge.infra.llm_client import AnthropicClient, OpenAICompatibleClient
from transbridge.infra.prompt_cache import (
    PROMPT_CACHE_METADATA_KEY,
    _encoding_for_model,
    attach_prompt_cache_directive,
    build_prompt_cache_key,
    estimate_prompt_tokens,
    prepare_openai_chat_cache_request,
)

_LONG_STABLE_SYSTEM = "Stable cache policy and translation instruction. " * 2500


class _OfflineEncoding:
    def encode(self, text: str) -> list[int]:
        return list(range(max(1, (len(text) + 3) // 4)))


class _OfflineTiktoken:
    @staticmethod
    def encoding_for_model(_model: str) -> _OfflineEncoding:
        return _OfflineEncoding()

    @staticmethod
    def get_encoding(_name: str) -> _OfflineEncoding:
        return _OfflineEncoding()


class _OfflineTokenizerTestCase(unittest.TestCase):
    """Provider mock 测试不得因 tiktoken 首次下载编码文件而访问网络。"""

    def setUp(self) -> None:
        super().setUp()
        _encoding_for_model.cache_clear()
        estimate_prompt_tokens.cache_clear()
        self._tokenizer_patch = patch(
            "transbridge.infra.prompt_cache.tiktoken",
            _OfflineTiktoken(),
        )
        self._tokenizer_patch.start()

    def tearDown(self) -> None:
        estimate_prompt_tokens.cache_clear()
        _encoding_for_model.cache_clear()
        self._tokenizer_patch.stop()
        super().tearDown()


def _fake_response(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def _fake_stream(chunks):
    cm = MagicMock()
    cm.__enter__.return_value = cm
    cm.__exit__.return_value = False
    chunks_iter = (SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=t))]) for t in chunks)
    cm.__iter__.return_value = iter(chunks_iter)
    return cm


def _make_openai_client(**overrides) -> OpenAICompatibleClient:
    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client._api_key = overrides.get("api_key", "k")
    client._base_url = overrides.get("base_url", "https://api.openai.com/v1")
    client._model = overrides.get("model", "gpt-4o")
    client._max_retries = 2
    client._lock = type("_L", (), {"__enter__": lambda s: None, "__exit__": lambda *a: None})()
    client._http_client = MagicMock()
    client._client = MagicMock()
    client._active_requests = 0
    return client


def _make_anthropic_client(**overrides) -> AnthropicClient:
    client = AnthropicClient.__new__(AnthropicClient)
    client._api_key = overrides.get("api_key", "k")
    client._model = overrides.get("model", "claude-3-7-sonnet-latest")
    client._max_retries = 2
    client._lock = type("_L", (), {"__enter__": lambda s: None, "__exit__": lambda *a: None})()
    client._http_client = MagicMock()
    client._client = MagicMock()
    client._active_requests = 0
    return client


def _stable_prefix_messages(system_text: str = _LONG_STABLE_SYSTEM, user_text: str = "Hi") -> list[dict]:
    """后处理 single_stable_prefix：唯一 SYSTEM(FINAL) -> USER。"""
    key = build_prompt_cache_key("pp.v1", system_text)
    sys_with = attach_prompt_cache_directive(
        {"role": "system", "content": system_text},
        cache_key=key,
        profile="single_stable_prefix",
        breakpoint="FINAL",
    )
    return [sys_with, {"role": "user", "content": user_text}]


def _plain_messages() -> list[dict]:
    """无任何内部元数据的普通消息。"""
    return [
        {"role": "system", "content": "You are a translator."},
        {"role": "user", "content": "Hello"},
    ]


# ── OpenAI ───────────────────────────────────────────────────────────────────


class TestOpenAIExplicitBreakpoint(_OfflineTokenizerTestCase):
    """官方 base_url + 显式断点模型（gpt-6.*）。"""

    @patch("transbridge.infra.llm_client.OpenAICompatibleClient._make_client")
    def test_chat_explicit_single_final(self, _make_client):
        _make_client.return_value = MagicMock()
        client = _make_openai_client(base_url="https://api.openai.com/v1", model="gpt-6")
        messages = _stable_prefix_messages()
        client._client.chat.completions.create.return_value = _fake_response("ok")
        # 规避 __init__ 未运行：cancel 依赖 _client 已由构造设置
        result = client.chat(messages, max_tokens=64)

        self.assertEqual(result, "ok")
        call_kwargs = client._client.chat.completions.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gpt-6")
        self.assertIsInstance(call_kwargs["messages"], list)
        # 显式断点：system 转 content block 并带 breakpoint
        sys_msg = call_kwargs["messages"][0]
        self.assertEqual(sys_msg["role"], "system")
        self.assertEqual(sys_msg["content"][0]["prompt_cache_breakpoint"], {"mode": "explicit"})
        # 非标准字段走 extra_body
        self.assertEqual(call_kwargs["extra_body"]["prompt_cache_options"], {"mode": "explicit"})
        self.assertIn("prompt_cache_key", call_kwargs["extra_body"])
        # 无内部元数据泄漏
        for m in call_kwargs["messages"]:
            self.assertNotIn(PROMPT_CACHE_METADATA_KEY, m)

    def test_chat_and_stream_share_conversion(self):
        client = _make_openai_client(base_url="https://api.openai.com/v1", model="gpt-6")
        messages = _stable_prefix_messages()

        client.chat(messages, max_tokens=64)
        chat_kwargs = client._client.chat.completions.create.call_args.kwargs

        client._client.chat.completions.create.return_value = _fake_stream(["a", "b"])
        received: list[str] = []
        client.chat_stream(messages, max_tokens=64, chunk_callback=received.append)
        stream_kwargs = client._client.chat.completions.create.call_args.kwargs

        self.assertEqual(chat_kwargs["messages"], stream_kwargs["messages"])
        self.assertEqual(chat_kwargs["extra_body"], stream_kwargs["extra_body"])
        for m in stream_kwargs["messages"]:
            self.assertNotIn(PROMPT_CACHE_METADATA_KEY, m)


class TestOpenAICacheValidation(_OfflineTokenizerTestCase):
    @patch("transbridge.infra.prompt_cache.tiktoken")
    def test_tokenizer_failure_disables_cache(self, tokenizer_mock):
        tokenizer_mock.encoding_for_model.side_effect = KeyError("unknown")
        tokenizer_mock.get_encoding.side_effect = RuntimeError("offline")
        _encoding_for_model.cache_clear()
        messages = _stable_prefix_messages()

        request = prepare_openai_chat_cache_request(
            model="unavailable-model-for-test",
            base_url="https://api.openai.com/v1",
            messages=messages,
        )

        self.assertEqual(request["cache_mode"], "disabled")
        self.assertEqual(request["request_options"], {})
        _encoding_for_model.cache_clear()

    @patch("transbridge.infra.prompt_cache.tiktoken", None)
    def test_missing_tokenizer_disables_cache_without_blocking_import(self):
        _encoding_for_model.cache_clear()
        messages = _stable_prefix_messages()

        request = prepare_openai_chat_cache_request(
            model="gpt-5.6",
            base_url="https://api.openai.com/v1",
            messages=messages,
        )

        self.assertEqual(request["cache_mode"], "disabled")
        self.assertEqual(request["request_options"], {})
        self.assertIsInstance(request["messages"][0]["content"], str)
        _encoding_for_model.cache_clear()

    def test_valid_translation_layered_marks_both_system_messages(self):
        key = build_prompt_cache_key("translation.v2", _LONG_STABLE_SYSTEM)
        messages = [
            attach_prompt_cache_directive(
                {"role": "system", "content": _LONG_STABLE_SYSTEM},
                cache_key=key,
                profile="translation_layered",
                breakpoint="A",
            ),
            attach_prompt_cache_directive(
                {"role": "system", "content": "<translation_mode>dialogue</translation_mode>"},
                cache_key=key,
                profile="translation_layered",
                breakpoint="B",
            ),
            {"role": "user", "content": "dynamic"},
        ]

        request = prepare_openai_chat_cache_request(
            model="gpt-5.6",
            base_url="https://api.openai.com/v1",
            messages=messages,
        )

        self.assertEqual(request["cache_mode"], "explicit_breakpoints")
        self.assertEqual(
            request["messages"][0]["content"][0]["prompt_cache_breakpoint"],
            {"mode": "explicit"},
        )
        self.assertEqual(
            request["messages"][1]["content"][0]["prompt_cache_breakpoint"],
            {"mode": "explicit"},
        )

    def test_short_stable_prefix_disables_cache_options(self):
        messages = _stable_prefix_messages(system_text="short stable prefix")

        request = prepare_openai_chat_cache_request(
            model="gpt-5.6",
            base_url="https://api.openai.com/v1",
            messages=messages,
        )

        self.assertEqual(request["cache_mode"], "disabled")
        self.assertEqual(request["request_options"], {})
        self.assertIsInstance(request["messages"][0]["content"], str)

    def test_final_on_user_is_invalid_and_disables_cache(self):
        key = build_prompt_cache_key("pp.v1", _LONG_STABLE_SYSTEM)
        messages = [
            {"role": "system", "content": _LONG_STABLE_SYSTEM},
            attach_prompt_cache_directive(
                {"role": "user", "content": "dynamic"},
                cache_key=key,
                profile="single_stable_prefix",
                breakpoint="FINAL",
            ),
        ]

        request = prepare_openai_chat_cache_request(
            model="gpt-5.6",
            base_url="https://api.openai.com/v1",
            messages=messages,
        )

        self.assertEqual(request["cache_mode"], "disabled")
        self.assertEqual(request["request_options"], {})

    def test_translation_a_only_is_invalid_and_disables_cache(self):
        key = build_prompt_cache_key("translation.v2", _LONG_STABLE_SYSTEM)
        messages = [
            attach_prompt_cache_directive(
                {"role": "system", "content": _LONG_STABLE_SYSTEM},
                cache_key=key,
                profile="translation_layered",
                breakpoint="A",
            ),
            {"role": "system", "content": "mode"},
            {"role": "user", "content": "dynamic"},
        ]

        request = prepare_openai_chat_cache_request(
            model="gpt-5.6",
            base_url="https://api.openai.com/v1",
            messages=messages,
        )

        self.assertEqual(request["cache_mode"], "disabled")
        self.assertEqual(request["request_options"], {})


class TestOpenAIAutomaticPrefix(_OfflineTokenizerTestCase):
    """官方 base_url + 自动前缀模型（gpt-4o 等未知/非显式模型）。"""

    def test_automatic_prefix_clean_messages(self):
        client = _make_openai_client(base_url="https://api.openai.com/v1", model="gpt-4o")
        messages = _stable_prefix_messages()
        client._client.chat.completions.create.return_value = _fake_response("ok")

        client.chat(messages, max_tokens=64)
        call_kwargs = client._client.chat.completions.create.call_args.kwargs

        # system 保持标准字符串内容，不加 breakpoint
        sys_msg = call_kwargs["messages"][0]
        self.assertEqual(sys_msg["role"], "system")
        self.assertIsInstance(sys_msg["content"], str)
        self.assertNotIn("prompt_cache_breakpoint", sys_msg)
        # 自动前缀：仅传 prompt_cache_key 到 extra_body
        self.assertNotIn("prompt_cache_options", call_kwargs["extra_body"])
        self.assertIn("prompt_cache_key", call_kwargs["extra_body"])
        for m in call_kwargs["messages"]:
            self.assertNotIn(PROMPT_CACHE_METADATA_KEY, m)


class TestOpenAINonOfficial(_OfflineTokenizerTestCase):
    """非官方兼容端点：只收到清理后的标准消息，无任何私有字段。"""

    def test_non_official_no_private_fields(self):
        client = _make_openai_client(base_url="https://gateway.example/v1", model="gpt-6")
        messages = _stable_prefix_messages()
        client._client.chat.completions.create.return_value = _fake_response("ok")

        client.chat(messages, max_tokens=64)
        call_kwargs = client._client.chat.completions.create.call_args.kwargs

        self.assertNotIn("extra_body", call_kwargs)
        self.assertNotIn("prompt_cache_key", call_kwargs)
        for m in call_kwargs["messages"]:
            self.assertNotIn(PROMPT_CACHE_METADATA_KEY, m)
            self.assertNotIn("prompt_cache_breakpoint", m)


class TestOpenAICacheRejectionRetry(_OfflineTokenizerTestCase):
    """缓存参数被拒时：一次降级（去掉缓存参数干净重试），第二次失败上抛。"""

    class CacheReject(Exception):
        def __init__(self, status_code, message):
            super().__init__(message)
            self.status_code = status_code
            self.message = message

    def test_retry_once_on_cache_400(self):
        client = _make_openai_client(base_url="https://api.openai.com/v1", model="gpt-6")
        messages = _stable_prefix_messages()

        def fake_create(**kwargs):
            # 首次带缓存参数 -> 400 cache 拒绝；第二次干净 -> 成功
            if "extra_body" in kwargs:
                raise self.CacheReject(400, "prompt_cache not supported")
            return _fake_response("ok")

        client._client.chat.completions.create.side_effect = fake_create

        result = client.chat(messages, max_tokens=64)
        self.assertEqual(result, "ok")
        calls = client._client.chat.completions.create.call_args_list
        self.assertEqual(len(calls), 2)
        # 第二次调用无缓存参数（无 extra_body），messages 为干净标准消息
        second = calls[1].kwargs
        self.assertNotIn("extra_body", second)
        for m in second["messages"]:
            self.assertNotIn(PROMPT_CACHE_METADATA_KEY, m)
            self.assertNotIn("prompt_cache_breakpoint", m)

    def test_second_failure_propagates(self):
        client = _make_openai_client(base_url="https://api.openai.com/v1", model="gpt-4o")
        messages = _stable_prefix_messages()

        def fake_create(**kwargs):
            raise self.CacheReject(400, "cache invalid")

        client._client.chat.completions.create.side_effect = fake_create

        with self.assertRaises(self.CacheReject):
            client.chat(messages, max_tokens=64)
        self.assertEqual(client._client.chat.completions.create.call_count, 2)

    def test_non_cache_error_no_retry(self):
        client = _make_openai_client(base_url="https://api.openai.com/v1", model="gpt-6")
        messages = _stable_prefix_messages()

        def fake_create(**kwargs):
            raise RuntimeError("boom")

        client._client.chat.completions.create.side_effect = fake_create

        with self.assertRaises(RuntimeError):
            client.chat(messages, max_tokens=64)
        self.assertEqual(client._client.chat.completions.create.call_count, 1)


# ── Anthropic ────────────────────────────────────────────────────────────────


class TestAnthropic(_OfflineTokenizerTestCase):
    def test_short_prefix_uses_system_blocks_without_cache_control(self):
        client = _make_anthropic_client()
        messages = _stable_prefix_messages(system_text="short stable prefix")
        client._client.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(text="ok")])

        client.chat(messages, max_tokens=64)

        system = client._client.messages.create.call_args.kwargs["system"]
        self.assertTrue(system)
        self.assertFalse(any("cache_control" in block for block in system))

    def test_chat_system_blocks_with_cache_control(self):
        client = _make_anthropic_client()
        messages = _stable_prefix_messages()
        client._client.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(text="ok")])

        result = client.chat(messages, max_tokens=64)
        self.assertEqual(result, "ok")

        call_kwargs = client._client.messages.create.call_args.kwargs
        system = call_kwargs["system"]
        # system 为 content blocks 列表，FINAL 块带 ephemeral cache_control
        self.assertIsInstance(system, list)
        sys_block = system[0]
        self.assertEqual(sys_block["type"], "text")
        self.assertEqual(sys_block["cache_control"], {"type": "ephemeral"})
        # user 消息不含缓存标记
        self.assertEqual([m["role"] for m in call_kwargs["messages"]], ["user"])
        for m in call_kwargs["messages"]:
            self.assertNotIn(PROMPT_CACHE_METADATA_KEY, m)
            self.assertNotIn("cache_control", m)

    def test_no_system_blocks_no_system_key(self):
        client = _make_anthropic_client()
        messages = [{"role": "user", "content": "Hi"}]
        client._client.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(text="ok")])

        client.chat(messages, max_tokens=64)
        call_kwargs = client._client.messages.create.call_args.kwargs
        self.assertNotIn("system", call_kwargs)
        self.assertEqual([m["role"] for m in call_kwargs["messages"]], ["user"])

    def test_chat_and_stream_share_conversion(self):
        client = _make_anthropic_client()
        messages = _stable_prefix_messages()

        client._client.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(text="x")])
        client.chat(messages, max_tokens=64)
        chat_kwargs = client._client.messages.create.call_args.kwargs

        stream_cm = MagicMock()
        stream_cm.__enter__.return_value = stream_cm
        stream_cm.__exit__.return_value = False
        stream_cm.text_stream = iter(["a", "b"])
        client._client.messages.stream.return_value = stream_cm

        received: list[str] = []
        client.chat_stream(messages, max_tokens=64, chunk_callback=received.append)
        stream_kwargs = client._client.messages.stream.call_args.kwargs

        self.assertEqual(chat_kwargs["system"], stream_kwargs["system"])
        self.assertEqual(chat_kwargs["messages"], stream_kwargs["messages"])
        self.assertEqual(received, ["a", "b"])

    def test_system_blocks_downgrade_to_string_once(self):
        client = _make_anthropic_client()
        messages = _stable_prefix_messages()

        class SDKVersionError(Exception):
            pass

        def fake_create(**kwargs):
            if isinstance(kwargs.get("system"), list):
                raise SDKVersionError("system must be a string")
            return SimpleNamespace(content=[SimpleNamespace(text="ok")])

        client._client.messages.create.side_effect = fake_create
        result = client.chat(messages, max_tokens=64)
        self.assertEqual(result, "ok")
        self.assertEqual(client._client.messages.create.call_count, 2)
        second = client._client.messages.create.call_args_list[1].kwargs
        self.assertIsInstance(second["system"], str)
        self.assertEqual(second["system"], _LONG_STABLE_SYSTEM)

    def test_cache_rejection_retries_once_without_cache_control(self):
        client = _make_anthropic_client()
        messages = _stable_prefix_messages()

        class CacheReject(Exception):
            def __init__(self):
                super().__init__("cache_control is not supported")
                self.status_code = 400
                self.message = "cache_control is not supported"

        def fake_create(**kwargs):
            system = kwargs.get("system", [])
            if any("cache_control" in block for block in system if isinstance(block, dict)):
                raise CacheReject()
            return SimpleNamespace(content=[SimpleNamespace(text="ok")])

        client._client.messages.create.side_effect = fake_create

        result = client.chat(messages, max_tokens=64)

        self.assertEqual(result, "ok")
        self.assertEqual(client._client.messages.create.call_count, 2)
        retry = client._client.messages.create.call_args_list[1].kwargs
        self.assertTrue(retry["system"])
        self.assertFalse(any("cache_control" in block for block in retry["system"]))

    def test_stream_cache_rejection_retries_once_without_cache_control(self):
        client = _make_anthropic_client()
        messages = _stable_prefix_messages()

        class CacheReject(Exception):
            def __init__(self):
                super().__init__("cache_control is not supported")
                self.status_code = 400
                self.message = "cache_control is not supported"

        def fake_stream(**kwargs):
            system = kwargs.get("system", [])
            if any("cache_control" in block for block in system if isinstance(block, dict)):
                raise CacheReject()
            stream = MagicMock()
            stream.__enter__.return_value = stream
            stream.__exit__.return_value = False
            stream.text_stream = iter(["a", "b"])
            return stream

        client._client.messages.stream.side_effect = fake_stream
        received: list[str] = []

        result = client.chat_stream(messages, max_tokens=64, chunk_callback=received.append)

        self.assertEqual(result, "ab")
        self.assertEqual(received, ["a", "b"])
        self.assertEqual(client._client.messages.stream.call_count, 2)
        retry = client._client.messages.stream.call_args_list[1].kwargs
        self.assertFalse(any("cache_control" in block for block in retry["system"]))


if __name__ == "__main__":
    unittest.main()
