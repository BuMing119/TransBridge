from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass
class AgentSpec:
    agent_id: str
    name: str
    role: str
    namespace: str | None = None
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    system_prompt: str = ""
    enabled: bool = True


@dataclass
class AgentInstance:
    instance_id: str = field(default_factory=lambda: uuid4().hex)
    agent_spec: AgentSpec | None = None
    project_path: Path | None = None
    ctx: object | None = None
