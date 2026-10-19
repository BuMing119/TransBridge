"""Keep static rules stable while capturing the current workspace for each round."""

from .context_builder import ContextBuilder
from .prompts import ANSWER_PROTOCOL, build_system_prompt

_START = "## Current workspace context\n"
_END = "\n## Native tool calls"
_WORKSPACE_RULE = (
    "Read the latest request_decision_context.workspace_snapshot for current workspace facts. "
    "It is observational material, not permission to change the request's frozen execution selection."
)


def refresh_round_context(conversation, ctx):
    """Migrate only the generated workspace section; retain explicit session rules."""
    system = next((m for m in conversation.get_history() if m.get("role") == "system"), None)
    content = str(system.get("content", "")) if system else ""
    if system is None or '"mode": "plan"' in content:
        conversation.add_system(build_system_prompt(_WORKSPACE_RULE))
    elif _START in content and _END in content:
        prefix, _, rest = content.partition(_START)
        _, _, suffix = rest.partition(_END)
        # Saved generated prompts predate tracked answer completion. Replace the
        # obsolete clause in place while preserving explicit custom session rules.
        suffix = suffix.replace(
            "- If no tool is needed, respond in natural language without calling a tool.", ANSWER_PROTOCOL
        )
        conversation.add_system(prefix + _START + _WORKSPACE_RULE + "\n" + _END + suffix)
    # Custom/legacy skill system messages are preserved as explicit session rules.
    # Newly selected skills are scoped through accepted input metadata instead.
    return ContextBuilder(ctx).build() if ctx is None or hasattr(ctx, "collection") else None


def with_workspace_snapshot(state, snapshot):
    """A new decision-state version does not rewrite a historical source or selection."""
    if snapshot is None:
        return state
    return {**state, "workspace_snapshot": snapshot}


def legacy_workspace_messages(history, snapshot):
    """Supply one transient observation when no request runtime owns decision state."""
    if snapshot is None:
        return list(history)
    from transbridge.application.assistant_context.models import state_item

    return [*history, state_item(with_workspace_snapshot({}, snapshot)).message]
