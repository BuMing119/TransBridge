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

    # ── 持久化 ──────────────────────────────────────────

    def save_to_file(self) -> None:
        config_path = get_config_file_path()
        config = configparser.ConfigParser()
        if os.path.exists(config_path):
            config.read(config_path, encoding="utf-8")
        if not config.has_section("llm"):
            config.add_section("llm")
        c = config
        c.set("llm", "provider", self.provider)
        c.set("llm", "api_key", self.api_key)
        c.set("llm", "base_url", self.base_url)
        c.set("llm", "model", self.model)
        c.set("llm", "max_concurrent", str(self.max_concurrent))
        c.set("llm", "max_tokens_per_batch", str(self.max_tokens_per_batch))
        c.set("llm", "max_output_tokens", str(self.max_output_tokens))
        c.set("llm", "term_priority", ",".join(self.term_priority))
        c.set("llm", "local_json_path", self.local_json_path)
        c.set("llm", "local_excel_path", self.local_excel_path)
        c.set("llm", "excel_original_col", self.excel_original_col)
        c.set("llm", "excel_translation_col", self.excel_translation_col)
        c.set("llm", "game_profile", self.game_profile)
        c.set("llm", "target_lang", self.target_lang)
        c.set("llm", "enable_semantic_match", str(self.enable_semantic_match))
        c.set("llm", "semantic_similarity_threshold", str(self.semantic_similarity_threshold))
        c.set("llm", "semantic_top_k", str(self.semantic_top_k))
        c.set("llm", "max_terms_per_batch", str(self.max_terms_per_batch))
        # Embedding
        c.set("llm", "embedding_mode", self.embedding.mode)
        c.set("llm", "embedding_provider", self.embedding.provider)
        c.set("llm", "embedding_model", self.embedding.model)
        c.set("llm", "embedding_api_key", self.embedding.api_key)
        c.set("llm", "embedding_base_url", self.embedding.base_url)
        c.set("llm", "embedding_local_model_path", self.embedding.local_model_path)
        # 后处理
        c.set("llm", "enable_post_process", str(self.enable_post_process))
        c.set("llm", "pp_enable_consistency_check", str(self.pp_enable_consistency_check))
        c.set("llm", "pp_enable_format_validation", str(self.pp_enable_format_validation))
        c.set("llm", "pp_enable_quality_gate", str(self.pp_enable_quality_gate))
        c.set("llm", "pp_quality_gate_batch_size", str(self.pp_quality_gate_batch_size))
        c.set("llm", "pp_enable_refinement", str(self.pp_enable_refinement))
        c.set("llm", "pp_refinement_batch_size", str(self.pp_refinement_batch_size))
        c.set("llm", "pp_enable_polish", str(self.pp_enable_polish))
        c.set("llm", "pp_polish_scope", self.pp_polish_scope)
        c.set("llm", "pp_polish_level", self.pp_polish_level)
        c.set("llm", "pp_polish_batch_size", str(self.pp_polish_batch_size))
        c.set("llm", "polish_preview_enabled", str(self.polish_preview_enabled))
        c.set("llm", "pp_enable_arbitration", str(self.pp_enable_arbitration))
        c.set("llm", "pp_strict_arbitration", str(self.pp_strict_arbitration))
        c.set("llm", "pp_arbitration_batch_size", str(self.pp_arbitration_batch_size))
        c.set("llm", "mixed_execution_order", self.mixed_execution_order)
        if self.action_rules:
            from src.transbridge.paratranz.config_manager import ActionRule
            rules_json = json.dumps(
                [r.to_dict() if isinstance(r, ActionRule) else r for r in self.action_rules],
                ensure_ascii=False,
            )
            c.set("llm", "action_rules", rules_json)
        # [guardrails]
        if not config.has_section("guardrails"):
            config.add_section("guardrails")
        c.set("guardrails", "enable_admin_confirm", str(self.guardrails_enable_admin_confirm))
        c.set("guardrails", "enable_input_validation", str(self.guardrails_enable_input_validation))
        c.set("guardrails", "enable_output_validation", str(self.guardrails_enable_output_validation))
        c.set("guardrails", "max_input_size", str(self.guardrails_max_input_size))
        c.set("guardrails", "write_require_confirm", str(self.guardrails_write_require_confirm))
        # [mcp]
        if not config.has_section("mcp"):
            config.add_section("mcp")
        c.set("mcp", "enabled", str(self.mcp_enabled))
        c.set("mcp", "transport", self.mcp_transport)
        c.set("mcp", "admin_tool_whitelist", self.mcp_admin_tool_whitelist)
        c.set("mcp", "write_tool_policy", self.mcp_write_tool_policy)
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
        g = config.get
        gi = config.getint
        gb = config.getboolean
        gf = config.getfloat
        obj.provider = g("llm", "provider", fallback=obj.provider)
        obj.api_key = g("llm", "api_key", fallback=obj.api_key)
        obj.base_url = g("llm", "base_url", fallback=obj.base_url)
        obj.model = g("llm", "model", fallback=obj.model)
        obj.max_concurrent = gi("llm", "max_concurrent", fallback=obj.max_concurrent)
        obj.max_tokens_per_batch = gi("llm", "max_tokens_per_batch", fallback=obj.max_tokens_per_batch)
        obj.max_output_tokens = gi("llm", "max_output_tokens", fallback=obj.max_output_tokens)
        p = g("llm", "term_priority", fallback="")
        if p:
            obj.term_priority = [x.strip() for x in p.split(",") if x.strip()]
        obj.local_json_path = g("llm", "local_json_path", fallback=obj.local_json_path)
        obj.local_excel_path = g("llm", "local_excel_path", fallback=obj.local_excel_path)
        obj.excel_original_col = g("llm", "excel_original_col", fallback=obj.excel_original_col)
        obj.excel_translation_col = g("llm", "excel_translation_col", fallback=obj.excel_translation_col)
        obj.game_profile = g("llm", "game_profile", fallback=obj.game_profile)
        obj.target_lang = g("llm", "target_lang", fallback=obj.target_lang)
        obj.enable_semantic_match = gb("llm", "enable_semantic_match", fallback=obj.enable_semantic_match)
        obj.semantic_similarity_threshold = gf("llm", "semantic_similarity_threshold", fallback=obj.semantic_similarity_threshold)
        obj.semantic_top_k = gi("llm", "semantic_top_k", fallback=obj.semantic_top_k)
        obj.max_terms_per_batch = gi("llm", "max_terms_per_batch", fallback=obj.max_terms_per_batch)
        # Embedding
        obj.embedding.mode = g("llm", "embedding_mode", fallback=obj.embedding.mode)
        obj.embedding.provider = g("llm", "embedding_provider", fallback=obj.embedding.provider)
        obj.embedding.model = g("llm", "embedding_model", fallback=obj.embedding.model)
        obj.embedding.api_key = g("llm", "embedding_api_key", fallback=obj.embedding.api_key)
        obj.embedding.base_url = g("llm", "embedding_base_url", fallback=obj.embedding.base_url)
        obj.embedding.local_model_path = g("llm", "embedding_local_model_path", fallback=obj.embedding.local_model_path)
        # 后处理
        obj.enable_post_process = gb("llm", "enable_post_process", fallback=obj.enable_post_process)
        obj.pp_enable_consistency_check = gb("llm", "pp_enable_consistency_check", fallback=obj.pp_enable_consistency_check)
        obj.pp_enable_format_validation = gb("llm", "pp_enable_format_validation", fallback=obj.pp_enable_format_validation)
        obj.pp_enable_quality_gate = gb("llm", "pp_enable_quality_gate", fallback=obj.pp_enable_quality_gate)
        obj.pp_quality_gate_batch_size = gi("llm", "pp_quality_gate_batch_size", fallback=obj.pp_quality_gate_batch_size)
        obj.pp_enable_refinement = gb("llm", "pp_enable_refinement", fallback=obj.pp_enable_refinement)
        obj.pp_refinement_batch_size = gi("llm", "pp_refinement_batch_size", fallback=obj.pp_refinement_batch_size)
        obj.pp_enable_polish = gb("llm", "pp_enable_polish", fallback=obj.pp_enable_polish)
        obj.pp_polish_scope = g("llm", "pp_polish_scope", fallback=obj.pp_polish_scope)
        obj.pp_polish_level = g("llm", "pp_polish_level", fallback=obj.pp_polish_level)
        obj.pp_polish_batch_size = gi("llm", "pp_polish_batch_size", fallback=obj.pp_polish_batch_size)
        obj.polish_preview_enabled = gb("llm", "polish_preview_enabled", fallback=obj.polish_preview_enabled)
        obj.pp_enable_arbitration = gb("llm", "pp_enable_arbitration", fallback=obj.pp_enable_arbitration)
        obj.pp_strict_arbitration = gb("llm", "pp_strict_arbitration", fallback=obj.pp_strict_arbitration)
        obj.pp_arbitration_batch_size = gi("llm", "pp_arbitration_batch_size", fallback=obj.pp_arbitration_batch_size)
        obj.mixed_execution_order = g("llm", "mixed_execution_order", fallback=obj.mixed_execution_order)
        rules_raw = g("llm", "action_rules", fallback="")
        if rules_raw:
            try:
                from src.transbridge.paratranz.config_manager import ActionRule
                rules_data = json.loads(rules_raw)
                obj.action_rules = [ActionRule.from_dict(d) for d in rules_data]
            except (json.JSONDecodeError, Exception):
                obj.action_rules = []
        # [guardrails]
        if config.has_section("guardrails"):
            obj.guardrails_enable_admin_confirm = gb("guardrails", "enable_admin_confirm", fallback=obj.guardrails_enable_admin_confirm)
            obj.guardrails_enable_input_validation = gb("guardrails", "enable_input_validation", fallback=obj.guardrails_enable_input_validation)
            obj.guardrails_enable_output_validation = gb("guardrails", "enable_output_validation", fallback=obj.guardrails_enable_output_validation)
            obj.guardrails_max_input_size = gi("guardrails", "max_input_size", fallback=obj.guardrails_max_input_size)
            obj.guardrails_write_require_confirm = gb("guardrails", "write_require_confirm", fallback=obj.guardrails_write_require_confirm)
        # [mcp]
        if config.has_section("mcp"):
            obj.mcp_enabled = gb("mcp", "enabled", fallback=obj.mcp_enabled)
            obj.mcp_transport = g("mcp", "transport", fallback=obj.mcp_transport)
            obj.mcp_admin_tool_whitelist = g("mcp", "admin_tool_whitelist", fallback=obj.mcp_admin_tool_whitelist)
            obj.mcp_write_tool_policy = g("mcp", "write_tool_policy", fallback=obj.mcp_write_tool_policy)
        return obj

    @staticmethod
    def get_ai_translator_dir(esp_stem: str) -> str:
        base_dir = get_data_dir()
        ai_dir = os.path.join(base_dir, "ai_translator", esp_stem)
        os.makedirs(ai_dir, exist_ok=True)
        return ai_dir
