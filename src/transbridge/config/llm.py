"""LLM translation configuration facade over the unified repository."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, fields
import hmac
import json
import os
from pathlib import Path
from typing import Any, ClassVar

from .paratranz_credentials import (
    CredentialRef,
    CredentialStorageError,
    CredentialStore,
    EnvironmentCredentialProvider,
    SecretValue,
    default_credential_store,
)
from .paths import get_data_dir
from .repository import ConfigRepository, ConfigSnapshot, default_config_repository

_LLM_REF = CredentialRef("TransBridge.LLM", "default")
_EMBEDDING_REF = CredentialRef("TransBridge.Embedding", "default")
_MCP_REF = CredentialRef("TransBridge.MCP", "default")


@dataclass
class EmbeddingConfig:
    """Embedding service settings (ADR-010: api/local/disabled)."""

    mode: str = "disabled"
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    api_key: str = field(default="", repr=False)
    base_url: str = ""
    local_model_path: str = ""
    credential_ref: CredentialRef = field(default=_EMBEDDING_REF, repr=False)


@dataclass
class LLMConfig:
    """Mutable compatibility facade backed by one versioned ``transbridge.ini``."""

    provider: str = "openai_compatible"
    api_key: str = field(default="", repr=False)
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    max_concurrent: int = 3
    llm_max_retries: int = 2
    max_tokens_per_batch: int = 2000
    max_output_tokens: int = 0
    temperature: float = 0.0
    term_priority: list[str] = field(default_factory=lambda: ["dynamic", "paratranz", "json", "csv", "excel"])
    local_json_path: str = ""
    local_csv_path: str = ""
    local_excel_path: str = ""
    excel_original_col: str = "A"
    excel_translation_col: str = "B"
    game_profile: str = "skyrim_se"
    target_lang: str = "zh_CN"
    retrieval_enabled: bool = True
    enable_semantic_match: bool = True
    semantic_similarity_threshold: float = 0.7
    semantic_top_k: int = 5
    max_terms_per_batch: int = 50
    bm25_weight: float = 0.5
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    enable_post_process: bool = True
    pp_strategy: str = "proofread"
    pp_enable_consistency_check: bool = True
    pp_enable_format_validation: bool = True
    pp_enable_quality_gate: bool = True
    pp_quality_gate_batch_size: int = 10
    pp_enable_refinement: bool = True
    pp_refinement_batch_size: int = 5
    pp_enable_polish: bool = False
    pp_polish_scope: str = "all"
    pp_polish_level: str = "moderate"
    pp_polish_batch_size: int = 5
    polish_preview_enabled: bool = False
    pp_enable_arbitration: bool = True
    pp_strict_arbitration: bool = False
    pp_arbitration_batch_size: int = 10
    action_rules: list[Any] = field(default_factory=list)
    workflow_profiles: dict[str, dict[str, object]] = field(default_factory=dict)
    mixed_execution_order: str = "serial"
    guardrails_enable_admin_confirm: bool = True
    guardrails_enable_input_validation: bool = True
    guardrails_enable_output_validation: bool = True
    guardrails_max_input_size: int = 102400
    guardrails_write_require_confirm: bool = False
    mcp_enabled: bool = False
    mcp_transport: str = "stdio"
    mcp_admin_tool_whitelist: str = ""
    mcp_write_tool_policy: str = "deny"
    mcp_auth_token: str = field(default="", repr=False)
    credential_ref: CredentialRef = field(default=_LLM_REF, repr=False)
    mcp_credential_ref: CredentialRef = field(default=_MCP_REF, repr=False)
    config_revision: int = field(default=0, init=False)
    _repository: ConfigRepository | None = field(default=None, init=False, repr=False)
    _credential_store: CredentialStore | None = field(default=None, init=False, repr=False)
    _environment: Mapping[str, str] | None = field(default=None, init=False, repr=False)

    _CONFIG_FIELDS: ClassVar[tuple[tuple[str, str, str], ...]] = (
        ("max_concurrent", "max_concurrent", "int"),
        ("llm_max_retries", "llm_max_retries", "int"),
        ("max_tokens_per_batch", "max_tokens_per_batch", "int"),
        ("max_output_tokens", "max_output_tokens", "int"),
        ("temperature", "temperature", "float"),
        ("local_json_path", "local_json_path", "str"),
        ("local_csv_path", "local_csv_path", "str"),
        ("local_excel_path", "local_excel_path", "str"),
        ("excel_original_col", "excel_original_col", "str"),
        ("excel_translation_col", "excel_translation_col", "str"),
        ("game_profile", "game_profile", "str"),
        ("target_lang", "target_lang", "str"),
        ("retrieval_enabled", "retrieval_enabled", "bool"),
        ("enable_semantic_match", "enable_semantic_match", "bool"),
        ("semantic_similarity_threshold", "semantic_similarity_threshold", "float"),
        ("semantic_top_k", "semantic_top_k", "int"),
        ("max_terms_per_batch", "max_terms_per_batch", "int"),
        ("bm25_weight", "bm25_weight", "float"),
        ("enable_post_process", "enable_post_process", "bool"),
        ("pp_strategy", "pp_strategy", "str"),
        ("pp_enable_consistency_check", "pp_enable_consistency_check", "bool"),
        ("pp_enable_format_validation", "pp_enable_format_validation", "bool"),
        ("pp_enable_quality_gate", "pp_enable_quality_gate", "bool"),
        ("pp_quality_gate_batch_size", "pp_quality_gate_batch_size", "int"),
        ("pp_enable_refinement", "pp_enable_refinement", "bool"),
        ("pp_refinement_batch_size", "pp_refinement_batch_size", "int"),
        ("pp_enable_polish", "pp_enable_polish", "bool"),
        ("pp_polish_scope", "pp_polish_scope", "str"),
        ("pp_polish_level", "pp_polish_level", "str"),
        ("pp_polish_batch_size", "pp_polish_batch_size", "int"),
        ("polish_preview_enabled", "polish_preview_enabled", "bool"),
        ("pp_enable_arbitration", "pp_enable_arbitration", "bool"),
        ("pp_strict_arbitration", "pp_strict_arbitration", "bool"),
        ("pp_arbitration_batch_size", "pp_arbitration_batch_size", "int"),
        ("mixed_execution_order", "mixed_execution_order", "str"),
    )
    _EMBEDDING_FIELDS: ClassVar[tuple[tuple[str, str, str], ...]] = (
        ("mode", "embedding_mode", "str"),
        ("provider", "embedding_provider", "str"),
        ("model", "embedding_model", "str"),
        ("base_url", "embedding_base_url", "str"),
        ("local_model_path", "embedding_local_model_path", "str"),
    )
    _GUARDRAILS_FIELDS: ClassVar[tuple[tuple[str, str, str], ...]] = (
        ("guardrails_enable_admin_confirm", "enable_admin_confirm", "bool"),
        ("guardrails_enable_input_validation", "enable_input_validation", "bool"),
        ("guardrails_enable_output_validation", "enable_output_validation", "bool"),
        ("guardrails_max_input_size", "max_input_size", "int"),
        ("guardrails_write_require_confirm", "write_require_confirm", "bool"),
    )
    _MCP_FIELDS: ClassVar[tuple[tuple[str, str, str], ...]] = (
        ("mcp_enabled", "enabled", "bool"),
        ("mcp_transport", "transport", "str"),
        ("mcp_admin_tool_whitelist", "admin_tool_whitelist", "str"),
        ("mcp_write_tool_policy", "write_tool_policy", "str"),
    )

    def copy_for_execution(self) -> LLMConfig:
        """Return a detached value snapshot without repository locks or providers."""

        values = {item.name: deepcopy(getattr(self, item.name)) for item in fields(self) if item.init}
        copied = type(self)(**values)
        copied.config_revision = self.config_revision
        return copied

    def save_to_file(
        self,
        *,
        repository: ConfigRepository | None = None,
        credential_store: CredentialStore | None = None,
    ) -> None:
        """Save through the repository; plaintext credentials never reach the INI."""

        repo = (
            repository
            or self._repository
            or default_config_repository(credential_store=credential_store or self._credential_store)
        )
        store = credential_store or self._credential_store or repo.credential_store
        _persist_secret(store, self.credential_ref, self.api_key, self._environment, "TRANSBRIDGE_LLM_API_KEY")
        _persist_secret(
            store,
            self.embedding.credential_ref,
            self.embedding.api_key,
            self._environment,
            "TRANSBRIDGE_EMBEDDING_API_KEY",
        )
        _persist_secret(
            store,
            self.mcp_credential_ref,
            self.mcp_auth_token,
            self._environment,
            "TRANSBRIDGE_MCP_AUTH_TOKEN",
        )
        llm: dict[str, Any | None] = {
            "credential_ref": self.credential_ref.target_name,
            "embedding_credential_ref": self.embedding.credential_ref.target_name,
            "term_priority": ",".join(self.term_priority),
            "action_rules": self._serialize_action_rules() or None,
            "workflow_profiles": self._serialize_workflow_profiles() or None,
        }
        if all(str(value).strip() for value in (self.provider, self.base_url, self.model)):
            llm.update({"provider": self.provider, "base_url": self.base_url, "model": self.model})
        llm.update({key: getattr(self, attr) for attr, key, _kind in self._CONFIG_FIELDS})
        llm["pp_strategy"] = _normalize_persisted_postprocess_strategy(self.pp_strategy)
        llm.update({key: getattr(self.embedding, attr) for attr, key, _kind in self._EMBEDDING_FIELDS})
        snapshot = repo.update_sections({
            "llm": llm,
            "guardrails": {key: getattr(self, attr) for attr, key, _kind in self._GUARDRAILS_FIELDS},
            "mcp": {
                **{key: getattr(self, attr) for attr, key, _kind in self._MCP_FIELDS},
                "credential_ref": self.mcp_credential_ref.target_name,
            },
        })
        self.config_revision = snapshot.revision
        self._repository = repo
        self._credential_store = store

    @classmethod
    def load_from_file(
        cls,
        *,
        repository: ConfigRepository | None = None,
        credential_store: CredentialStore | None = None,
        environment: Mapping[str, str] | None = None,
        config_path: str | os.PathLike[str] | None = None,
    ) -> LLMConfig:
        store = credential_store or default_credential_store()
        if repository is not None:
            repo = repository
            store = credential_store or repository.credential_store
        elif config_path is not None:
            repo = ConfigRepository(path=config_path, legacy_path=config_path, credential_store=store)
        else:
            repo = default_config_repository(credential_store=store)
        snapshot = repo.load()
        obj = cls()
        obj.config_revision = snapshot.revision
        obj._repository = repo
        obj._credential_store = store
        obj._environment = environment
        obj.provider = snapshot.value("llm", "provider", obj.provider) or obj.provider
        obj.base_url = snapshot.value("llm", "base_url", obj.base_url) or ""
        obj.model = snapshot.value("llm", "model", obj.model) or ""
        obj.credential_ref = _credential_ref(snapshot.value("llm", "credential_ref"), _LLM_REF)
        obj.embedding.credential_ref = _credential_ref(
            snapshot.value("llm", "embedding_credential_ref"), _EMBEDDING_REF
        )
        obj.mcp_credential_ref = _credential_ref(snapshot.value("mcp", "credential_ref"), _MCP_REF)
        for attr, key, kind in cls._CONFIG_FIELDS:
            _load_field(obj, snapshot, "llm", attr, key, kind)
        for attr, key, kind in cls._EMBEDDING_FIELDS:
            _load_field(obj.embedding, snapshot, "llm", attr, key, kind)
        for attr, key, kind in cls._GUARDRAILS_FIELDS:
            _load_field(obj, snapshot, "guardrails", attr, key, kind)
        for attr, key, kind in cls._MCP_FIELDS:
            _load_field(obj, snapshot, "mcp", attr, key, kind)
        priorities = snapshot.value("llm", "term_priority", "") or ""
        if priorities:
            obj.term_priority = [part.strip() for part in priorities.split(",") if part.strip()]
            if obj.term_priority == ["dynamic", "paratranz", "json", "excel"]:
                obj.term_priority.insert(3, "csv")
        obj.action_rules = cls._load_action_rules(snapshot.value("llm", "action_rules", "") or "")
        obj.workflow_profiles = cls._load_workflow_profiles(snapshot.value("llm", "workflow_profiles", "") or "")
        from transbridge.application.translation.ai_execution_profile import apply_profile_settings

        apply_profile_settings(obj, "translate")
        obj.api_key = _resolve_secret(store, obj.credential_ref, environment, "TRANSBRIDGE_LLM_API_KEY")
        obj.embedding.api_key = _resolve_secret(
            store,
            obj.embedding.credential_ref,
            environment,
            "TRANSBRIDGE_EMBEDDING_API_KEY",
        )
        obj.mcp_auth_token = _resolve_secret(
            store,
            obj.mcp_credential_ref,
            environment,
            "TRANSBRIDGE_MCP_AUTH_TOKEN",
        )
        return obj

    def _serialize_action_rules(self) -> str:
        if not self.action_rules:
            return ""
        from transbridge.paratranz.config_manager import ActionRule

        payload = [rule.to_dict() if isinstance(rule, ActionRule) else rule for rule in self.action_rules]
        return json.dumps(payload, ensure_ascii=False, allow_nan=False)

    @staticmethod
    def _load_action_rules(raw: str) -> list[Any]:
        if not raw:
            return []
        try:
            from transbridge.paratranz.config_manager import ActionRule

            data = json.loads(raw)
            if not isinstance(data, list):
                return []
            return [ActionRule.from_dict(item) for item in data if isinstance(item, dict)]
        except (ImportError, json.JSONDecodeError, TypeError, ValueError):
            return []

    def _serialize_workflow_profiles(self) -> str:
        if not self.workflow_profiles:
            return ""
        payload = deepcopy(self.workflow_profiles)
        for settings in payload.values():
            if isinstance(settings, dict) and "pp_strategy" in settings:
                settings["pp_strategy"] = _normalize_persisted_postprocess_strategy(settings["pp_strategy"])
        return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)

    @staticmethod
    def _load_workflow_profiles(raw: str) -> dict[str, dict[str, object]]:
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        profiles: dict[str, dict[str, object]] = {}
        for name, settings in data.items():
            if name not in {"translate", "polish", "mixed"} or not isinstance(settings, dict):
                continue
            normalized = dict(settings)
            if "pp_strategy" in normalized:
                normalized["pp_strategy"] = _normalize_persisted_postprocess_strategy(normalized["pp_strategy"])
            profiles[str(name)] = normalized
        return profiles

    @staticmethod
    def get_ai_translator_dir(esp_stem: str) -> str:
        path = Path(get_data_dir()) / "ai_translator" / esp_stem
        path.mkdir(parents=True, exist_ok=True)
        return str(path)


def _normalize_persisted_postprocess_strategy(value: object) -> str:
    """Write the canonical strategy while accepting values from legacy callers."""

    return "strict" if str(value).strip().casefold() == "strict" else "proofread"


def _load_field(
    target: object,
    snapshot: ConfigSnapshot,
    section: str,
    attr: str,
    key: str,
    kind: str,
) -> None:
    raw = snapshot.value(section, key)
    if raw is None:
        return
    try:
        value: object
        if kind == "bool":
            normalized = raw.strip().casefold()
            if normalized not in {"true", "false", "yes", "no", "1", "0", "on", "off"}:
                raise ValueError
            value = normalized in {"true", "yes", "1", "on"}
        elif kind == "int":
            value = int(raw)
        elif kind == "float":
            value = float(raw)
        else:
            value = raw
    except ValueError:
        return
    setattr(target, attr, value)


def _credential_ref(raw: str | None, default: CredentialRef) -> CredentialRef:
    if not raw:
        return default
    service, separator, account = raw.rpartition(":")
    return CredentialRef(service, account) if separator and service and account else default


def _resolve_secret(
    store: CredentialStore,
    reference: CredentialRef,
    environment: Mapping[str, str] | None,
    variable: str,
) -> str:
    value = EnvironmentCredentialProvider(environment, variable).get(reference)
    if value is None:
        try:
            value = store.get(reference)
        except CredentialStorageError:
            return ""
    return value._reveal_for_request() if value is not None else ""


def _persist_secret(
    store: CredentialStore,
    reference: CredentialRef,
    plaintext: str,
    environment: Mapping[str, str] | None,
    variable: str,
) -> None:
    if not plaintext:
        return
    if EnvironmentCredentialProvider(environment, variable).get(reference) is not None:
        return
    if not store.capability.writable:
        raise CredentialStorageError("secure credential storage is not writable")
    secret = SecretValue(plaintext)
    store.set(reference, secret)
    verified = store.get(reference)
    if verified is None or not hmac.compare_digest(verified._reveal_for_request(), plaintext):
        raise CredentialStorageError("secure credential verification failed")
