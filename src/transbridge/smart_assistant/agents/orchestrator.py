import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ..execution_engine import StepResult

logger = logging.getLogger(__name__)


@dataclass
class Subtask:
    task_id: int
    agent_type: str
    action: str
    input_data: dict = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)


class Orchestrator:
    """编排 Agent：任务分解 → 调度映射 → 结果汇总。"""

    def __init__(self, agent_registry, tool_registry, llm_client):
        self._agents = agent_registry
        self._tools = tool_registry
        self._llm = llm_client

    def decompose_task(self, user_request: str, ctx: Any) -> list[Subtask]:
        agents_desc = []
        for a in self._agents.list_enabled():
            agents_desc.append(f"- {a.agent_id}: {a.role}")
        prompt = (
            "你是一个任务编排器。请将用户请求分解为子任务列表。\n\n"
            "可用 Agent:\n" + "\n".join(agents_desc) + "\n\n"
            f"用户请求: {user_request}\n\n"
            "返回纯 JSON 数组（不要包含 markdown 标记），每个元素格式:\n"
            '{"task_id": <int>, "agent_type": "<agent_id>", "action": "<一句话描述>", '
            '"input_data": {<参数>}, "depends_on": [<依赖的task_id列表>]}\n\n'
            "注意:\n"
            "- task_id 从 1 开始递增\n"
            "- depends_on 为空列表表示无依赖（可并行）\n"
            "- 需要翻译的请求分配给 translator agent\n"
            "- 需要检查质量的请求分配给 proofreader agent\n"
            "- 复杂请求可分解为多步: 翻译 → 校对\n"
            "- 如果只有一个简单请求，返回单个任务的数组"
        )
        try:
            resp = self._llm.chat([{"role": "user", "content": prompt}])
            text = resp.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1])
            tasks_data = json.loads(text)
            if not isinstance(tasks_data, list):
                tasks_data = [tasks_data]
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("任务分解失败，使用单任务兜底: %s", exc)
            tasks_data = [{
                "task_id": 1, "agent_type": "orchestrator",
                "action": user_request, "input_data": {}, "depends_on": [],
            }]
        subtasks = []
        for td in tasks_data:
            subtasks.append(Subtask(
                task_id=td.get("task_id", len(subtasks) + 1),
                agent_type=td.get("agent_type", "orchestrator"),
                action=td.get("action", ""),
                input_data=td.get("input_data", {}),
                depends_on=td.get("depends_on", []),
            ))
        return subtasks

    def map_to_steps(self, subtasks: list[Subtask], ctx: Any) -> list[dict[str, Any]]:
        # CR2 / TODO: LLM prompt 中 "action" 字段是人类可读描述（如"翻译DLC1条目"），
        # 而非有效工具名。当前回退到 agent_spec.tools[0] 作为兜底方案。
        # 长期修复应在 LLM prompt 中增加 "tool_name" 字段要求，
        # 并在 decompose_task() 的 JSON 输出 schema 中同步添加。
        steps = []
        for st in subtasks:
            agent_spec = self._agents.get(st.agent_type)
            if agent_spec is None:
                continue
            from .agent_spec import AgentInstance
            instance = AgentInstance(
                agent_spec=agent_spec,
                project_path=getattr(ctx, 'project_path', None),
                ctx=ctx,
            )
            tool_name = getattr(st, 'action', '')  # m33: 'tool_name' not a SubTask attr, removed dead getattr
            if not tool_name and agent_spec.tools:
                tool_name = agent_spec.tools[0]  # E4: fallback to first tool
            step = {
                "id": st.task_id,
                "tool": tool_name,
                "agent": st.agent_type,
                "agent_instance_id": instance.instance_id,
                "args": st.input_data,
                "depends_on": st.depends_on,
                "retry": True,
                "_instance": instance,
            }
            steps.append(step)
        return steps

    def summarize_results(self, results: list[StepResult], user_request: str) -> str:
        results_text = []
        for r in results:
            status = "成功" if r.success else "失败"
            results_text.append(f"- [{status}] {r.tool}: {r.message}")
        prompt = (
            f"用户请求: {user_request}\n\n"
            "执行结果:\n" + "\n".join(results_text) + "\n\n"
            "请用简洁的中文汇总执行结果（2-5 句话）。"
        )
        try:
            return self._llm.chat([{"role": "user", "content": prompt}])
        except Exception as exc:
            logger.warning("结果汇总失败: %s", exc)
            return "\n".join(results_text)
