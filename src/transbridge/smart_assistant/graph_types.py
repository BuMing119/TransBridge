from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NodeSpec:
    node_id: str
    node_type: str  # "action" | "condition" | "loop" | "human_confirm"


@dataclass
class ActionNode(NodeSpec):
    tool: str = ""
    args: dict = field(default_factory=dict)
    agent: str | None = None
    retry: bool = True


@dataclass
class ConditionNode(NodeSpec):
    condition: str = ""       # "result.data['score'] < 0.7"
    true_node: str = ""
    false_node: str = ""


@dataclass
class LoopNode(NodeSpec):
    sub_nodes: list = field(default_factory=list)
    max_iterations: int = 10
    exit_condition: str = ""  # "result.data.get('all_passed')"


@dataclass
class HumanConfirmNode(NodeSpec):
    prompt: str = ""
    choices: list[str] = field(default_factory=lambda: ["继续", "跳过", "终止"])
    timeout_seconds: int = 300
    default_choice: str = "继续"


@dataclass
class EdgeSpec:
    from_node: str
    to_node: str
    edge_type: str = "always"  # "always" | "conditional" | "loop_back"


@dataclass
class GraphSpec:
    graph_id: str
    nodes: list = field(default_factory=list)
    edges: list[EdgeSpec] = field(default_factory=list)
    entry_node: str = ""


@dataclass
class Checkpoint:
    graph_id: str
    current_node_id: str
    completed_results: dict = field(default_factory=dict)
    graph_state: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "graph_id": self.graph_id,
            "current_node_id": self.current_node_id,
            "completed_results": self.completed_results,
            "graph_state": self.graph_state,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Checkpoint":
        return cls(
            graph_id=d.get("graph_id", ""),
            current_node_id=d.get("current_node_id", ""),
            completed_results=d.get("completed_results", {}),
            graph_state=d.get("graph_state", {}),
            timestamp=d.get("timestamp", ""),
        )
