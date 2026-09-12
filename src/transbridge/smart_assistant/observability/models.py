from dataclasses import dataclass, field
from datetime import datetime
import threading

from transbridge.infra.llm_usage import LlmUsage


@dataclass
class TokenStats:
    input_tokens: int = 0
    output_tokens: int = 0
    by_model: dict = field(default_factory=dict)
    usage_attempts: dict[str, LlmUsage] = field(default_factory=dict)
    _usage_lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def add_usage(self, usage: LlmUsage) -> bool:
        """Only the first final callback for an attempt contributes to accounting."""
        with self._usage_lock:
            if usage.attempt_id in self.usage_attempts:
                return False
            self.usage_attempts[usage.attempt_id] = usage
            return True

    def usage_totals(self) -> dict:
        with self._usage_lock:
            usages = tuple(self.usage_attempts.values())
        reported = [u for u in usages if u.source == "reported"]
        return {
            "known_input_tokens": sum(u.input_tokens or 0 for u in reported),
            "known_output_tokens": sum(u.output_tokens or 0 for u in reported),
            "known_cache_read_tokens": sum(u.cache_read_tokens or 0 for u in reported),
            "known_cache_write_tokens": sum(u.cache_write_tokens or 0 for u in reported),
            "attempts": len(usages),
            "incomplete_attempts": sum(u.completeness != "complete" for u in usages),
            "unknown_input_attempts": sum(u.input_tokens is None for u in usages),
            "unknown_output_attempts": sum(u.output_tokens is None for u in usages),
            "unknown_cache_read_attempts": sum(u.cache_read_tokens is None for u in usages),
            "unknown_cache_write_attempts": sum(u.cache_write_tokens is None for u in usages),
        }

    def add(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        if model not in self.by_model:
            self.by_model[model] = {"input": 0, "output": 0}
        self.by_model[model]["input"] += input_tokens
        self.by_model[model]["output"] += output_tokens

    def to_dict(self) -> dict:
        with self._usage_lock:
            usages = [usage.to_dict() for usage in self.usage_attempts.values()]
            totals = self.usage_totals()
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "by_model": self.by_model,
            "legacy_source": "estimated",
            "usage_attempts": usages,
            "usage_totals": totals,
        }


@dataclass
class ToolCallRecord:
    timestamp: str = ""
    tool_name: str = ""
    input_summary: str = ""
    output_summary: str = ""
    duration_ms: int = 0
    success: bool = False
    retry_count: int = 0


@dataclass
class ReActRound:
    round_num: int = 0
    llm_input_tokens: int = 0
    llm_output_summary: str = ""
    tools: list[str] = field(default_factory=list)
    duration_ms: int = 0


@dataclass
class ConversationTrace:
    conv_id: str = ""
    rounds: list = field(default_factory=list)
    tools_called: list = field(default_factory=list)
    token_stats: TokenStats = field(default_factory=TokenStats)
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    finished_at: str = ""

    def to_dict(self) -> dict:
        return {
            "conv_id": self.conv_id,
            "rounds": [r.__dict__ for r in self.rounds],
            "tools_called": [t.__dict__ for t in self.tools_called],
            "token_stats": self.token_stats.to_dict(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
