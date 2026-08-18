from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    name: str
    role: str
    namespace: str | None = None
    tools: tuple[str, ...] = field(default_factory=tuple)
    skills: tuple[str, ...] = field(default_factory=tuple)
    system_prompt: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "skills", tuple(self.skills))


@dataclass
class AgentInstance:
    instance_id: str = field(default_factory=lambda: uuid4().hex)
    agent_spec: AgentSpec | None = None
    project_path: Path | None = None
    ctx: object | None = None
