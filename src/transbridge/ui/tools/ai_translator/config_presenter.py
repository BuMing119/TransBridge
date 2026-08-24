"""Qt-free configuration orchestration for the AI translator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from transbridge.paratranz.config_manager import LLMConfig


class ConfigViewPort(Protocol):
    def render_config(self, config: LLMConfig) -> None: ...

    def update_config(self, config: LLMConfig) -> LLMConfig: ...


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    level: str
    title: str
    message: str


class ConfigPresenter:
    """Maps the persisted config to/from a narrow view port."""

    def __init__(self, view: ConfigViewPort) -> None:
        self._view = view

    def load(self) -> LLMConfig:
        config = LLMConfig.load_from_file()
        self._view.render_config(config)
        return config

    def build(self) -> LLMConfig:
        return self._view.update_config(LLMConfig())

    def save(self) -> LLMConfig:
        config = self._view.update_config(LLMConfig.load_from_file())
        config.save_to_file()
        return config

    def test_connection(self) -> ConnectionTestResult:
        config = self.build()
        if not config.api_key:
            return ConnectionTestResult("warning", "测试连接", "请先填写 API Key。")
        if not config.model:
            return ConnectionTestResult("warning", "测试连接", "请先填写模型名。")
        try:
            from transbridge.infra.llm_client import create_llm_client

            reply = create_llm_client(config).chat(
                [{"role": "user", "content": "Say 'OK' in one word."}],
                max_tokens=10,
            )
            return ConnectionTestResult("info", "测试连接", f"连接成功！模型回复：{reply}")
        except Exception as exc:
            return ConnectionTestResult("critical", "测试连接失败", str(exc))
