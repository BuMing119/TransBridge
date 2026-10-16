"""Public local service availability, without exposing credentials to UI code."""

from dataclasses import dataclass
import logging
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceAvailability:
    model_name: str
    ready: bool
    missing_fields: tuple[str, ...] = ()
    message: str = ""


def service_availability(config) -> ServiceAvailability:
    """Validate the same local requirements used before creating the model client."""
    model = str(getattr(config, "model", "") or "").strip()
    missing, labels = [], []
    for name, label in (("model", "模型名称"), ("base_url", "服务地址"), ("api_key", "API Key")):
        if not str(getattr(config, name, "") or "").strip():
            missing.append(name)
            labels.append(label)
    if "base_url" not in missing:
        try:
            endpoint = urlsplit(str(config.base_url))
            valid_address = endpoint.scheme in {"http", "https"} and bool(endpoint.hostname)
        except ValueError:
            valid_address = False
        if not valid_address:
            missing.append("base_url")
            labels.append("有效的 HTTP(S) 服务地址")
    message = "请在设置 → AI 服务中配置" + "、".join(labels) + "。" if missing else "AI 服务配置就绪。"
    return ServiceAvailability(model, not missing, tuple(missing), message)


def read_service_availability() -> ServiceAvailability:
    """Resolve credentials inside the service boundary; return only safe status fields."""
    try:
        from transbridge.config.llm import LLMConfig

        return service_availability(LLMConfig.load_from_file())
    except Exception:
        logger.exception("读取智能助手服务配置失败")
        return ServiceAvailability("", False, ("configuration",), "无法读取 AI 服务配置，请打开设置检查后重试。")
