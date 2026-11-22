# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Editable AI service and glossary defaults used by the settings center."""

from urllib.parse import urlsplit

TERM_SOURCES = ("dynamic", "paratranz", "json", "csv", "excel")


def ordered_term_sources(saved):
    """Include every source once when displaying old or partial preferences."""
    return list(dict.fromkeys(key for key in (*saved, *TERM_SOURCES) if key in TERM_SOURCES))


def apply_service_preferences(config, updates):
    values = dict(updates)
    if "provider" in values and values["provider"] not in ("openai_compatible", "anthropic"):
        raise ValueError("供应商必须为 openai_compatible 或 anthropic")
    for field in ("model", "base_url"):
        if field in values:
            values[field] = values[field].strip()
    if "temperature" in values:
        temperature = values["temperature"]
        if type(temperature) not in (float, int) or not 0 <= temperature <= 2 or round(temperature, 2) != temperature:
            raise ValueError("Temperature 必须在 0 到 2 之间，最多两位小数")
    if "max_output_tokens" in values:
        tokens = values["max_output_tokens"]
        if type(tokens) is not int or not 0 <= tokens <= 1_000_000:
            raise ValueError("输出 Token 上限必须为 0 到 1000000 之间的整数")
    for field, value in values.items():
        if hasattr(config, field):
            setattr(config, field, value)


def apply_term_preferences(config, updates):
    for field, value in updates.items():
        if field == "term_priority":
            if len(value) != len(TERM_SOURCES) or set(value) != set(TERM_SOURCES):
                raise ValueError(f"请提供全部术语来源的优先顺序，每项一次：{', '.join(TERM_SOURCES)}")
            config.term_priority = list(value)
        elif hasattr(config, field):
            setattr(config, field, value.strip())


def is_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not parsed.username


def validate_llm_preferences(config):
    base_url = str(getattr(config, "base_url", "") or "").strip()
    if getattr(config, "provider", "") == "anthropic":
        from transbridge.infra.llm_request_config import resolve_anthropic_endpoint

        try:
            resolve_anthropic_endpoint(base_url)
        except ValueError as exc:
            return str(exc)
    elif base_url and not is_http_url(base_url):
        return "AI 服务 Base URL 必须是有效的 HTTP(S) 地址。"
    embedding_url = str(getattr(getattr(config, "embedding", None), "base_url", "") or "").strip()
    if embedding_url and not is_http_url(embedding_url):
        return "Embedding Base URL 必须是有效的 HTTP(S) 地址。"
    for attr, label in (("excel_original_col", "原文列"), ("excel_translation_col", "译文列")):
        value = str(getattr(config, attr, "") or "").strip()
        if not value.isascii() or not value.isalpha():
            return f"Excel {label}必须使用字母列名。"
    return None
