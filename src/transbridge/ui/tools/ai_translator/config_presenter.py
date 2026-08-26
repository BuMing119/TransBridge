"""Qt-free configuration orchestration for the AI translator."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from transbridge.application.translation.ai_execution_profile import (
    AiExecutionProfile,
    AiWorkflowPreset,
    apply_profile_settings,
    ensure_workflow_profiles,
    store_profile_settings,
)
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
        self._active_preset: AiWorkflowPreset = "translate"
        self._profiles: dict[str, dict[str, object]] = {}

    def load(self) -> LLMConfig:
        config = LLMConfig.load_from_file()
        self._profiles = deepcopy(ensure_workflow_profiles(config))
        apply_profile_settings(config, self._active_preset)
        self._view.render_config(config)
        return config

    def build(self) -> LLMConfig:
        return self._capture_active_profile()

    def save(self) -> LLMConfig:
        config = self._capture_active_profile()
        apply_profile_settings(config, "translate")
        try:
            config.save_to_file()
        finally:
            apply_profile_settings(config, self._active_preset)
        return config

    def switch_preset(self, preset: AiWorkflowPreset) -> LLMConfig:
        if preset == self._active_preset:
            return self.build()
        config = self._capture_active_profile()
        self._active_preset = preset
        apply_profile_settings(config, preset)
        self._view.render_config(config)
        return config

    def _capture_active_profile(self) -> LLMConfig:
        config = LLMConfig.load_from_file()
        config.workflow_profiles = deepcopy(self._profiles)
        apply_profile_settings(config, self._active_preset)
        config = self._view.update_config(config)
        store_profile_settings(config, self._active_preset)
        self._profiles = deepcopy(config.workflow_profiles)
        return config

    def execution_profile(self) -> AiExecutionProfile:
        return AiExecutionProfile.from_config(self._active_preset, self.build())

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
