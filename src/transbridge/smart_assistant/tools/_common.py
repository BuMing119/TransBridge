"""工具模块共享函数 — LLM 配置加载。"""

def load_llm_config():
    """从 LLMConfig.load_from_file() 加载配置。"""
    from src.transbridge.infra.config import LLMConfig
    return LLMConfig.load_from_file()
