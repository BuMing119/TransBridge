from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TokenStats:
    input_tokens: int = 0
    output_tokens: int = 0
    by_model: dict = field(default_factory=dict)

    def add(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        if model not in self.by_model:
            self.by_model[model] = {"input": 0, "output": 0}
        self.by_model[model]["input"] += input_tokens
        self.by_model[model]["output"] += output_tokens

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "by_model": self.by_model,
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
            "rounds": [r.__dict__ if hasattr(r, '__dict__') else r for r in self.rounds],
            "tools_called": [t.__dict__ if hasattr(t, '__dict__') else t for t in self.tools_called],
            "token_stats": self.token_stats.to_dict(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
