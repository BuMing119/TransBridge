from .agent_spec import AgentSpec


class AgentRegistry:
    _agents: dict[str, AgentSpec] = {}

    @classmethod
    def register(cls, spec: AgentSpec) -> None:
        cls._agents[spec.agent_id] = spec

    @classmethod
    def get(cls, agent_id: str) -> AgentSpec | None:
        return cls._agents.get(agent_id)

    @classmethod
    def list_all(cls) -> list[AgentSpec]:
        return list(cls._agents.values())

    @classmethod
    def list_enabled(cls) -> list[AgentSpec]:
        return [a for a in cls._agents.values() if a.enabled]

    @classmethod
    def enable(cls, agent_id: str) -> None:
        spec = cls._agents.get(agent_id)
        if spec:
            spec.enabled = True

    @classmethod
    def disable(cls, agent_id: str) -> None:
        spec = cls._agents.get(agent_id)
        if spec:
            spec.enabled = False

    @classmethod
    def init_presets(cls) -> None:
        cls.register(AgentSpec(
            agent_id="translator",
            name="翻译 Agent",
            role="你是一个专业的游戏 Mod 翻译 Agent。负责将英文文本翻译为中文，严格遵循术语库的标准译名。遇到不确定的术语时主动查询术语库。",
            namespace="translator",
            tools=["lookup_terms", "translate_entries", "get_collection_summary"],
            skills=["translate_with_terms"],
            system_prompt="你是 TransBridge 翻译引擎。请严格按照术语库的标准译名翻译，保持原文格式标签不变。",
        ))
        cls.register(AgentSpec(
            agent_id="proofreader",
            name="校对 Agent",
            role="你是一个专业的翻译校对 Agent。负责检查译文的一致性和格式正确性，但不直接修改译文。",
            namespace="proofreader",
            tools=["check_quality", "lookup_terms", "get_collection_summary"],
            skills=[],
            system_prompt="你是 TransBridge 校对引擎。请检查译文质量，发现术语不一致、格式错误时报告具体位置和建议。",
        ))
        cls.register(AgentSpec(
            agent_id="orchestrator",
            name="编排 Agent",
            role="你是一个翻译任务编排 Agent。负责分析用户请求，将复杂任务分解为子任务，分配给翻译或校对 Agent 执行，并汇总结果。",
            namespace=None,
            tools=["get_collection_summary", "lookup_terms", "check_quality",
                   "translate_entries", "export_json", "write_back"],
            skills=[],
            system_prompt="你是 TransBridge 编排引擎。分析用户意图，制定执行计划，调度合适的 Agent 完成任务，最后汇总呈现结果。",
        ))
