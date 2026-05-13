"""LLM 翻译与 Embedding 配置。"""

import json
import os
import configparser
from dataclasses import dataclass, field

from .paths import get_config_file_path, get_data_dir


@dataclass
class EmbeddingConfig:
    """Embedding 服务配置（ADR-010 三模式：api/local/disabled）。"""
    mode: str = "disabled"
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    api_key: str = ""
    base_url: str = ""
    local_model_path: str = ""


@dataclass
class LLMConfig:
    """LLM 翻译功能配置，独立于 ParatranzConfig，共享同一 INI 文件的 [llm] 节。"""

    provider: str = "openai_compatible"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    max_concurrent: int = 3
    max_tokens_per_batch: int = 2000
    max_output_tokens: int = 0
    term_priority: list = field(default_factory=lambda: ["dynamic", "paratranz", "json", "excel"])
    local_json_path: str = ""
    local_excel_path: str = ""
    excel_original_col: str = "A"
    excel_translation_col: str = "B"
    game_profile: str = "skyrim_se"
    target_lang: str = "zh_CN"

    # 向量检索
    enable_semantic_match: bool = True
    semantic_similarity_threshold: float = 0.7
    semantic_top_k: int = 5
    max_terms_per_batch: int = 50

    # Embedding（引用独立配置对象）
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)

    # 后处理
    enable_post_process: bool = True
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

    # 混合模式
    action_rules: list = field(default_factory=list)
    mixed_execution_order: str = "serial"

    # 安全护栏 (FR7.13.8)
    guardrails_enable_admin_confirm: bool = True
    guardrails_enable_input_validation: bool = True
    guardrails_enable_output_validation: bool = True
    guardrails_max_input_size: int = 102400
    guardrails_write_require_confirm: bool = False

    # MCP Server (FR7.13.5)
    mcp_enabled: bool = False
    mcp_transport: str = "stdio"
    mcp_admin_tool_whitelist: str = ""
    mcp_write_tool_policy: str = "deny"
    mcp_auth_token: str = ""

    # ── 配置字段描述表 ─────────────────────────────────
    # 驱动 save_to_file / load_from_file，消除样板代码
    # 格式: (section, attr_name, config_key, getter_name)
    # getter_name: "get" | "getint" | "getboolean" | "getfloat"
    _CONFIG_FIELDS = [
        # ── LLM 基础 ──
        ("llm", "provider", "provider", "get"),
        ("llm", "api_key", "api_key", "get"),
        ("llm", "base_url", "base_url", "get"),
        ("llm", "model", "model", "get"),
        ("llm", "max_concurrent", "max_concurrent", "getint"),
        ("llm", "max_tokens_per_batch", "max_tokens_per_batch", "getint"),
        ("llm", "max_output_tokens", "max_output_tokens", "getint"),
        ("llm", "local_json_path", "local_json_path", "get"),
        ("llm", "local_excel_path", "local_excel_path", "get"),
        ("llm", "excel_original_col", "excel_original_col", "get"),
        ("llm", "excel_translation_col", "excel_translation_col", "get"),
        ("llm", "game_profile", "game_profile", "get"),
        ("llm", "target_lang", "target_lang", "get"),
        ("llm", "enable_semantic_match", "enable_semantic_match", "getboolean"),
        ("llm", "semantic_similarity_threshold", "semantic_similarity_threshold", "getfloat"),
        ("llm", "semantic_top_k", "semantic_top_k", "getint"),
        ("llm", "max_terms_per_batch", "max_terms_per_batch", "getint"),
        # ── 后处理 ──
        ("llm", "enable_post_process", "enable_post_process", "getboolean"),
        ("llm", "pp_enable_consistency_check", "pp_enable_consistency_check", "getboolean"),
        ("llm", "pp_enable_format_validation", "pp_enable_format_validation", "getboolean"),
        ("llm", "pp_enable_quality_gate", "pp_enable_quality_gate", "getboolean"),
        ("llm", "pp_quality_gate_batch_size", "pp_quality_gate_batch_size", "getint"),
        ("llm", "pp_enable_refinement", "pp_enable_refinement", "getboolean"),
        ("llm", "pp_refinement_batch_size", "pp_refinement_batch_size", "getint"),
        ("llm", "pp_enable_polish", "pp_enable_polish", "getboolean"),
        ("llm", "pp_polish_scope", "pp_polish_scope", "get"),
        ("llm", "pp_polish_level", "pp_polish_level", "get"),
        ("llm", "pp_polish_batch_size", "pp_polish_batch_size", "getint"),
        ("llm", "polish_preview_enabled", "polish_preview_enabled", "getboolean"),
        ("llm", "pp_enable_arbitration", "pp_enable_arbitration", "getboolean"),
        ("llm", "pp_strict_arbitration", "pp_strict_arbitration", "getboolean"),
        ("llm", "pp_arbitration_batch_size", "pp_arbitration_batch_size", "getint"),
        ("llm", "mixed_execution_order", "mixed_execution_order", "get"),
    ]

    # Embedding 子对象字段: (attr_name, config_key, getter_name)
    _EMBEDDING_FIELDS = [
        ("mode", "embedding_mode", "get"),
        ("provider", "embedding_provider", "get"),
        ("model", "embedding_model", "get"),
        ("api_key", "embedding_api_key", "get"),
        ("base_url", "embedding_base_url", "get"),
        ("local_model_path", "embedding_local_model_path", "get"),
    ]

    # Guardrails 节字段
    _GUARDRAILS_FIELDS = [
        ("guardrails", "guardrails_enable_admin_confirm", "enable_admin_confirm", "getboolean"),
        ("guardrails", "guardrails_enable_input_validation", "enable_input_validation", "getboolean"),
        ("guardrails", "guardrails_enable_output_validation", "enable_output_validation", "getboolean"),
        ("guardrails", "guardrails_max_input_size", "max_input_size", "getint"),
        ("guardrails", "guardrails_write_require_confirm", "write_require_confirm", "getboolean"),
    ]

    # MCP 节字段
    _MCP_FIELDS = [
        ("mcp", "mcp_enabled", "enabled", "getboolean"),
        ("mcp", "mcp_transport", "transport", "get"),
        ("mcp", "mcp_admin_tool_whitelist", "admin_tool_whitelist", "get"),
        ("mcp", "mcp_write_tool_policy", "write_tool_policy", "get"),
        ("mcp", "mcp_auth_token", "auth_token", "get"),
    ]

    @staticmethod
    def _ensure_section(config: configparser.ConfigParser, section: str) -> None:
        if not config.has_section(section):
            config.add_section(section)

    # ── 持久化 ──────────────────────────────────────────

    # WARNING: API Key 以明文写入 INI 文件，生产环境应使用系统密钥链（如 keyring /
    # Windows Credential Manager / macOS Keychain）存储敏感凭据。
    def save_to_file(self) -> None:
        config_path = get_config_file_path()
        config = configparser.ConfigParser()
        if os.path.exists(config_path):
            config.read(config_path, encoding="utf-8")
        c = config

        # 主配置字段（由 _CONFIG_FIELDS 表驱动）
        for section, attr, key, _getter in self._CONFIG_FIELDS:
            self._ensure_section(config, section)
            val = getattr(self, attr)
            if isinstance(val, list):
                c.set(section, key, ",".join(str(v) for v in val))
            elif isinstance(val, bool):
                c.set(section, key, str(val))
            else:
                c.set(section, key, str(val))

        # term_priority: 列表字段
        c.set("llm", "term_priority", ",".join(self.term_priority))

        # Embedding 子对象
        for attr, key, _getter in self._EMBEDDING_FIELDS:
            c.set("llm", key, str(getattr(self.embedding, attr)))

        # action_rules: JSON 序列化
        if self.action_rules:
            from src.transbridge.paratranz.config_manager import ActionRule
            rules_json = json.dumps(
                [r.to_dict() if isinstance(r, ActionRule) else r for r in self.action_rules],
                ensure_ascii=False,
            )
            c.set("llm", "action_rules", rules_json)

        # Guardrails 节
        for section, attr, key, _getter in self._GUARDRAILS_FIELDS:
            self._ensure_section(config, section)
            c.set(section, key, str(getattr(self, attr)))

        # MCP 节
        for section, attr, key, _getter in self._MCP_FIELDS:
            self._ensure_section(config, section)
            c.set(section, key, str(getattr(self, attr)))

        with open(config_path, "w", encoding="utf-8") as f:
            config.write(f)

    @classmethod
    def load_from_file(cls) -> "LLMConfig":
        config_path = get_config_file_path()
        obj = cls()
        if not os.path.exists(config_path):
            return obj
        config = configparser.ConfigParser()
        config.read(config_path, encoding="utf-8")
        if not config.has_section("llm"):
            return obj

        # 主配置字段（由 _CONFIG_FIELDS 表驱动）
        for section, attr, key, getter_name in cls._CONFIG_FIELDS:
            if not config.has_section(section):
                continue
            getter = getattr(config, getter_name)
            current = getattr(obj, attr)
            setattr(obj, attr, getter(section, key, fallback=current))

        # term_priority: 列表字段
        p = config.get("llm", "term_priority", fallback="")
        if p:
            obj.term_priority = [x.strip() for x in p.split(",") if x.strip()]

        # Embedding 子对象
        for attr, key, getter_name in cls._EMBEDDING_FIELDS:
            getter = getattr(config, getter_name)
            current = getattr(obj.embedding, attr)
            setattr(obj.embedding, attr, getter("llm", key, fallback=current))

        # action_rules: JSON 反序列化
        rules_raw = config.get("llm", "action_rules", fallback="")
        if rules_raw:
            try:
                from src.transbridge.paratranz.config_manager import ActionRule
                rules_data = json.loads(rules_raw)
                obj.action_rules = [ActionRule.from_dict(d) for d in rules_data]
            except (json.JSONDecodeError, Exception):
                obj.action_rules = []

        # Guardrails 节
        for section, attr, key, getter_name in cls._GUARDRAILS_FIELDS:
            if config.has_section(section):
                getter = getattr(config, getter_name)
                current = getattr(obj, attr)
                setattr(obj, attr, getter(section, key, fallback=current))

        # MCP 节
        for section, attr, key, getter_name in cls._MCP_FIELDS:
            if config.has_section(section):
                getter = getattr(config, getter_name)
                current = getattr(obj, attr)
                setattr(obj, attr, getter(section, key, fallback=current))

        return obj

    @staticmethod
    def get_ai_translator_dir(esp_stem: str) -> str:
        base_dir = get_data_dir()
        ai_dir = os.path.join(base_dir, "ai_translator", esp_stem)
        os.makedirs(ai_dir, exist_ok=True)
        return ai_dir
