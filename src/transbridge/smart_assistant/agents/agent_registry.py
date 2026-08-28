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
    AGENT_TRANSLATOR,
    AGENT_PROOFREADER,
    AGENT_ORCHESTRATOR,
    AGENT_PARSER,
    AGENT_EDITOR,
    AGENT_PARATRANZ,
    AGENT_WRITER,
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
                expanded.extend(spec.name for spec in ns_tools if spec.available and not spec.deprecated)
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
            agent_id: replace(spec, tools=cls._expand_wildcard(spec.tools)) for agent_id, spec in cls._agents.items()
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
        cls.register(
            AgentSpec(
                agent_id=AGENT_TRANSLATOR,
                name="翻译 Agent",
                role=(
                    "You are a professional game mod localization agent. Translate source text into the configured "
                    "target language and strictly follow the glossary's approved terminology."
                ),
                namespace="translator",
                tools=["translator:*"],
                skills=["translate_with_terms"],
                system_prompt=(
                    "You are the TransBridge translation engine. Follow approved "
                    "glossary terms strictly and preserve all "
                    "source formatting tags."
                ),
            )
        )
        cls.register(
            AgentSpec(
                agent_id=AGENT_PROOFREADER,
                name="校对 Agent",
                role=(
                    "You are a professional translation proofreading agent. Check "
                    "translation consistency and formatting "
                    "and perform LLM post-processing when requested."
                ),
                namespace="proofreader",
                tools=["proofreader:*", "translator:get_task_status", "translator:stop_task"],
                skills=[],
                system_prompt=(
                    "You are the TransBridge proofreading engine. Check translation quality "
                    "and report exact locations and "
                    "recommendations for terminology or formatting issues."
                ),
            )
        )
        cls.register(
            AgentSpec(
                agent_id=AGENT_ORCHESTRATOR,
                name="编排 Agent",
                role=(
                    "You are a translation orchestration agent. Analyze user requests, "
                    "break complex work into subtasks, "
                    "and dispatch each subtask to the appropriate agent."
                ),
                namespace=None,
                tools=[
                    "default:*",
                    "editor:get_visible_entries",
                    "default:get_statistics",
                    "translator:get_translation_config",
                    "translator:get_task_status",
                    "paratranz:list_projects",
                    "paratranz:get_project_info",
                    "proofreader:run_postprocess",
                ],
                skills=[],
                system_prompt=(
                    "You are the TransBridge orchestration engine. Analyze user intent and "
                    "create an execution plan. Use "
                    "these meta-tools to describe sub-agent dispatch: parse/manage_entries/translate/run_postprocess/"
                    "sync_paratranz/write/query_state."
                ),
            )
        )

        # ── 新增 Agent (Story 13) ─────────────────────────────
        cls.register(
            AgentSpec(
                agent_id=AGENT_PARSER,
                name="解析 Agent",
                role=(
                    "Parse translation source files (ESP/EET/XT/SST/JSON/Strings) and load them into translation "
                    "collections."
                ),
                namespace="parser",
                tools=["parser:*"],
                skills=[],
                system_prompt=(
                    "You are the TransBridge parsing engine. Parse translation source files "
                    "from the paths supplied by the "
                    "user."
                ),
            )
        )
        cls.register(
            AgentSpec(
                agent_id=AGENT_EDITOR,
                name="编辑 Agent",
                role=(
                    "Manage translation entries by filtering, searching, selecting, editing "
                    "translations, setting stages, "
                    "and managing labels."
                ),
                namespace="editor",
                tools=["editor:*"],
                skills=[],
                system_prompt=(
                    "You are the TransBridge editing engine. Filter, edit, and mark translation "
                    "entries as requested by the "
                    "user."
                ),
            )
        )
        cls.register(
            AgentSpec(
                agent_id=AGENT_PARATRANZ,
                name="ParaTranz Agent",
                role=(
                    "Interact with ParaTranz for project management, uploads, downloads, "
                    "terminology synchronization, and "
                    "difference comparison."
                ),
                namespace="paratranz",
                tools=["paratranz:*"],
                skills=[],
                system_prompt=(
                    "You are the TransBridge ParaTranz integration engine. Manage cloud translation projects and "
                    "terminology."
                ),
            )
        )
        cls.register(
            AgentSpec(
                agent_id=AGENT_WRITER,
                name="写回 Agent",
                role=(
                    "Write translation results back to source files (ESP/EET/XT/Strings). "
                    "User confirmation is required "
                    "before writing."
                ),
                namespace="writer",
                tools=["writer:*"],
                skills=[],
                system_prompt=(
                    "You are the TransBridge write-back engine. Write-back operations require "
                    "administrator permission and "
                    "user confirmation; execute them cautiously."
                ),
            )
        )
