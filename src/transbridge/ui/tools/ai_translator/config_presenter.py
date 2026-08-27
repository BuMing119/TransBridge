"""Qt-free configuration orchestration for the AI translator."""

from __future__ import annotations

from collections.abc import Callable
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
from transbridge.application.translation.custom_workflow_profile import CustomWorkflowProfile
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
        self._custom_mode = False
        self._active_custom_profile: CustomWorkflowProfile | None = None
        self._persist_custom: Callable[[CustomWorkflowProfile], None] | None = None

    @property
    def active_custom_profile(self) -> CustomWorkflowProfile | None:
        return self._active_custom_profile

    def load(self) -> LLMConfig:
        config = LLMConfig.load_from_file()
        self._profiles = deepcopy(ensure_workflow_profiles(config))
        apply_profile_settings(config, self._active_preset)
        self._view.render_config(config)
        return config

    def build(self) -> LLMConfig:
        if self._active_custom_profile is not None:
            config, _profile = self._capture_active_custom()
            return config
        if self._custom_mode:
            return self._capture_empty_custom()
        return self._capture_active_profile()

    def save(self) -> LLMConfig:
        if self._active_custom_profile is not None:
            config, profile = self._capture_active_custom()
            if self._persist_custom is not None:
                self._persist_custom(profile)
            self._save_global_fields_from_custom(config)
            return config
        if self._custom_mode:
            config = self._capture_empty_custom()
            self._save_global_fields_from_custom(config)
            return config
        config = self._capture_active_profile()
        apply_profile_settings(config, "translate")
        try:
            config.save_to_file()
        finally:
            apply_profile_settings(config, self._active_preset)
        return config

    def switch_preset(self, preset: AiWorkflowPreset) -> LLMConfig:
        was_custom = self._custom_mode
        if self._custom_mode:
            self.save()
            self._exit_custom()
        if was_custom:
            self._active_preset = preset
            config = LLMConfig.load_from_file()
            config.workflow_profiles = deepcopy(self._profiles)
            apply_profile_settings(config, preset)
            self._view.render_config(config)
            return config
        if preset == self._active_preset:
            return self.build()
        config = self._capture_active_profile()
        self._active_preset = preset
        apply_profile_settings(config, preset)
        self._view.render_config(config)
        return config

    def activate_custom(
        self,
        profile: CustomWorkflowProfile,
        persist: Callable[[CustomWorkflowProfile], None],
    ) -> LLMConfig:
        if not self._custom_mode:
            self._capture_active_profile()
        self._custom_mode = True
        self._active_custom_profile = profile
        self._persist_custom = persist
        config = LLMConfig.load_from_file()
        config.workflow_profiles = deepcopy(self._profiles)
        execution = profile.apply_to(config)
        self._view.render_config(execution)
        return execution

    def clear_custom(self) -> None:
        if not self._custom_mode:
            self._capture_active_profile()
        self._custom_mode = True
        self._active_custom_profile = None
        self._persist_custom = None

    def _exit_custom(self) -> None:
        self._custom_mode = False
        self._active_custom_profile = None
        self._persist_custom = None

    def _capture_active_profile(self) -> LLMConfig:
        config = LLMConfig.load_from_file()
        config.workflow_profiles = deepcopy(self._profiles)
        apply_profile_settings(config, self._active_preset)
        config = self._view.update_config(config)
        store_profile_settings(config, self._active_preset)
        self._profiles = deepcopy(config.workflow_profiles)
        return config

    def _capture_active_custom(self) -> tuple[LLMConfig, CustomWorkflowProfile]:
        profile = self._active_custom_profile
        if profile is None:  # pragma: no cover - guarded by callers
            raise RuntimeError("no custom workflow profile is active")
        config = LLMConfig.load_from_file()
        config.workflow_profiles = deepcopy(self._profiles)
        execution = profile.apply_to(config)
        execution = self._view.update_config(execution)
        updated = CustomWorkflowProfile.from_config(
            profile.name,
            profile.base_mode,
            execution,
            description=profile.description,
            profile_id=profile.id,
        )
        self._active_custom_profile = updated
        return execution, updated

    def _capture_empty_custom(self) -> LLMConfig:
        config = LLMConfig.load_from_file()
        config.workflow_profiles = deepcopy(self._profiles)
        return self._view.update_config(config)

    def _save_global_fields_from_custom(self, execution: LLMConfig) -> None:
        global_config = LLMConfig.load_from_file()
        global_config.workflow_profiles = deepcopy(self._profiles)
        for field in (
            "provider",
            "target_lang",
            "model",
            "api_key",
            "base_url",
            "local_json_path",
            "local_csv_path",
            "local_excel_path",
            "excel_original_col",
            "excel_translation_col",
            "term_priority",
        ):
            setattr(global_config, field, deepcopy(getattr(execution, field)))
        global_config.embedding = deepcopy(execution.embedding)
        global_config.save_to_file()

    def execution_profile(self) -> AiExecutionProfile:
        preset = (
            self._active_custom_profile.base_mode if self._active_custom_profile is not None else self._active_preset
        )
        return AiExecutionProfile.from_config(preset, self.build())

    def test_connection(self) -> ConnectionTestResult:
        config = self.build()
        if not config.api_key:
            return ConnectionTestResult("warning", "测试 LLM 连接", "请先填写 LLM API Key。")
        if not config.model:
            return ConnectionTestResult("warning", "测试 LLM 连接", "请先填写 LLM 模型名。")
        try:
            from transbridge.infra.llm_client import create_llm_client

            reply = create_llm_client(config).chat(
                [{"role": "user", "content": "Say 'OK' in one word."}],
                max_tokens=10,
            )
            return ConnectionTestResult("info", "LLM 连接成功", f"模型回复：{reply}")
        except Exception as exc:
            return ConnectionTestResult("critical", "LLM 连接失败", str(exc))

    def test_embedding_connection(self, config: LLMConfig | None = None) -> ConnectionTestResult:
        """Run an explicit, user-triggered Embedding capability check."""

        config = self.build() if config is None else config
        embedding = config.embedding
        mode = str(getattr(embedding, "mode", "disabled") or "disabled").casefold()
        if mode == "disabled":
            return ConnectionTestResult("info", "语义检索已关闭", "当前仅使用精确和字面术语匹配。")
        if mode == "api":
            provider = str(getattr(embedding, "provider", "")).strip().casefold()
            if provider not in {"openai", "custom", "api"}:
                return ConnectionTestResult("warning", "检查语义检索", "请选择受支持的 Embedding API 服务商。")
            if not str(getattr(embedding, "api_key", "")).strip():
                return ConnectionTestResult("warning", "检查语义检索", "请填写独立的 Embedding API Key。")
            if not str(getattr(embedding, "model", "")).strip():
                return ConnectionTestResult("warning", "检查语义检索", "请填写 Embedding API 模型名。")
            if not str(getattr(embedding, "base_url", "")).strip():
                return ConnectionTestResult("warning", "检查语义检索", "请填写独立的 Embedding Base URL。")
        elif mode != "local":
            return ConnectionTestResult("warning", "检查语义检索", f"不支持的 Embedding 模式：{mode}")

        try:
            from transbridge.infra.embedding_client import create_embedding_client

            client = create_embedding_client(config)
            if not client.available:
                return ConnectionTestResult(
                    "critical",
                    "语义检索不可用",
                    client.error_message or "Embedding 客户端初始化失败。",
                )
            vectors = client.encode(["TransBridge semantic retrieval check"])
            if getattr(vectors, "shape", (0, 0))[0] != 1:
                raise RuntimeError("Embedding 服务未返回预期的一条向量")
            dimension = int(vectors.shape[1])
            backend = "本地模型" if mode == "local" else "API 服务"
            return ConnectionTestResult("info", "语义检索可用", f"{backend}编码成功，向量维度 {dimension}。")
        except Exception as exc:
            return ConnectionTestResult("critical", "语义检索检查失败", str(exc))
