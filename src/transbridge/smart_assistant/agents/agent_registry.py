from dataclasses import replace

from ..tool_registry import ToolRegistry
from .agent_spec import AgentSpec

# ── Agent ID 常量 (QA-007) ──────────────────────────────────────
AGENT_TRANSLATOR = "translator"
AGENT_PROOFREADER = "proofreader"
AGENT_ORCHESTRATOR = "orchestrator"
AGENT_PARSER = "parser"
AGENT_EDITOR = "editor"
AGENT_PARATRANZ = "paratranz"
AGENT_WRITER = "writer"

# 如需新增 Agent，请添加常量并同步更新 init_presets()
_ALL_AGENT_IDS = frozenset({
    AGENT_TRANSLATOR, AGENT_PROOFREADER, AGENT_ORCHESTRATOR,
    AGENT_PARSER, AGENT_EDITOR, AGENT_PARATRANZ, AGENT_WRITER,
})


class AgentRegistry:
    _agents: dict[str, AgentSpec] = {}
    _finalized = False

    @staticmethod
    def _expand_wildcard(tools) -> tuple[str, ...]:
        """Resolve namespace references against the completed tool registry."""
        expanded: list[str] = []
        for t in tools:
            if t.endswith(":*"):
                ns = t[:-2]
                ns_tools = ToolRegistry.list_namespace(ns)
                expanded.extend(
                    spec.name for spec in ns_tools if spec.available and not spec.deprecated
                )
            elif ":" in t:
                namespace, name = t.split(":", 1)
                spec = ToolRegistry.get(name, namespace=namespace)
                if spec is not None and spec.available and not spec.deprecated:
                    expanded.append(spec.name)
            else:
                spec = ToolRegistry.get(t)
                if spec is None or (spec.available and not spec.deprecated):
                    expanded.append(t)
        return tuple(dict.fromkeys(expanded))

    @classmethod
    def register(cls, spec: AgentSpec) -> None:
        if cls._finalized:
            raise RuntimeError("AgentRegistry 已冻结")
        cls._agents[spec.agent_id] = spec

    @classmethod
    def finalize(cls) -> None:
        """Resolve tool capabilities once, after all tool registration completes."""
        if cls._finalized:
            return
        cls._agents = {
            agent_id: replace(spec, tools=cls._expand_wildcard(spec.tools))
            for agent_id, spec in cls._agents.items()
        }
        cls._finalized = True

    @classmethod
    def _resolved(cls, spec: AgentSpec | None) -> AgentSpec | None:
        if spec is None or cls._finalized:
            return spec
        return replace(spec, tools=cls._expand_wildcard(spec.tools))

    @classmethod
    def get(cls, agent_id: str) -> AgentSpec | None:
        return cls._resolved(cls._agents.get(agent_id))

    @classmethod
    def list_all(cls) -> list[AgentSpec]:
        return [cls._resolved(spec) for spec in cls._agents.values()]

    @classmethod
    def list_enabled(cls) -> list[AgentSpec]:
        return [spec for spec in cls.list_all() if spec.enabled]

    @classmethod
    def enable(cls, agent_id: str) -> None:
        spec = cls._agents.get(agent_id)
        if spec:
            cls._agents[agent_id] = replace(spec, enabled=True)

    @classmethod
    def disable(cls, agent_id: str) -> None:
        spec = cls._agents.get(agent_id)
        if spec:
            cls._agents[agent_id] = replace(spec, enabled=False)

    @classmethod
    def init_presets(cls) -> None:
        if cls._finalized:
            return
        # ── 更新现有 Agent (Story 13) ─────────────────────────
        cls.register(AgentSpec(
            agent_id=AGENT_TRANSLATOR,
            name="翻译 Agent",
            role="你是一个专业的游戏 Mod 翻译 Agent。负责将英文文本翻译为中文，严格遵循术语库的标准译名。",
            namespace="translator",
            tools=["translator:*"],
            skills=["translate_with_terms"],
            system_prompt="你是 TransBridge 翻译引擎。请严格按照术语库的标准译名翻译，保持原文格式标签不变。",
        ))
        cls.register(AgentSpec(
            agent_id=AGENT_PROOFREADER,
            name="校对 Agent",
            role="你是一个专业的翻译校对 Agent。负责检查译文一致性和格式正确性，也可执行 LLM 后处理。",
            namespace="proofreader",
            tools=["proofreader:*", "translator:get_task_status", "translator:stop_task"],
            skills=[],
            system_prompt="你是 TransBridge 校对引擎。请检查译文质量，发现术语不一致、格式错误时报告具体位置和建议。",
        ))
        cls.register(AgentSpec(
            agent_id=AGENT_ORCHESTRATOR,
            name="编排 Agent",
            role="你是一个翻译任务编排 Agent。分析用户请求，将复杂任务分解为子任务并调度合适的 Agent 执行。",
            namespace=None,
            tools=["default:*", "editor:get_visible_entries", "default:get_statistics",
                   "translator:get_translation_config", "translator:get_task_status",
                   "paratranz:list_projects", "paratranz:get_project_info",
                   "proofreader:run_postprocess"],
            skills=[],
            system_prompt="你是 TransBridge 编排引擎。分析用户意图，制定执行计划。使用元工具描述调度子Agent：parse/manage_entries/translate/run_postprocess/sync_paratranz/write/query_state。",
        ))

        # ── 新增 Agent (Story 13) ─────────────────────────────
        cls.register(AgentSpec(
            agent_id=AGENT_PARSER,
            name="解析 Agent",
            role="负责解析各类翻译源文件（ESP/EET/XT/SST/JSON/Strings）并加载到翻译集合。",
            namespace="parser",
            tools=["parser:*"],
            skills=[],
            system_prompt="你是 TransBridge 解析引擎。请根据用户提供的文件路径解析翻译源文件。",
        ))
        cls.register(AgentSpec(
            agent_id=AGENT_EDITOR,
            name="编辑 Agent",
            role="负责管理翻译条目：筛选、搜索、选择、编辑翻译、设置阶段、管理标签。",
            namespace="editor",
            tools=["editor:*"],
            skills=[],
            system_prompt="你是 TransBridge 编辑引擎。请按用户需求筛选、编辑和标记翻译条目。",
        ))
        cls.register(AgentSpec(
            agent_id=AGENT_PARATRANZ,
            name="ParaTranz Agent",
            role="负责与 ParaTranz 平台交互：项目管理、上传下载、术语同步、对比差异。",
            namespace="paratranz",
            tools=["paratranz:*"],
            skills=[],
            system_prompt="你是 TransBridge ParaTranz 集成引擎。请管理云端翻译项目和术语库。",
        ))
        cls.register(AgentSpec(
            agent_id=AGENT_WRITER,
            name="写回 Agent",
            role="负责将翻译结果写回到源文件（ESP/EET/XT/Strings）。写回前必须经过用户确认。",
            namespace="writer",
            tools=["writer:*"],
            skills=[],
            system_prompt="你是 TransBridge 写回引擎。写回操作需要管理员权限和用户确认，请谨慎执行。",
        ))
