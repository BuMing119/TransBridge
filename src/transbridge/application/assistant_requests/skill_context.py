"""Immutable, user-selected workflow instructions scoped to their accepted request."""

from collections.abc import Mapping


def snapshot_skill_context(value):
    """Validate and detach the small public input contract before admission."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("技能说明格式无效，请重新选择技能。")
    name, prompt = value.get("name"), value.get("prompt")
    display_name = value.get("display_name", name)
    if not isinstance(name, str) or not name.strip() or not isinstance(prompt, str):
        raise ValueError("技能名称或说明无效，请重新选择技能。")
    if not isinstance(display_name, str):
        raise ValueError("技能显示名称无效，请重新选择技能。")
    return {"name": name.strip(), "prompt": prompt, "display_name": display_name}


def request_skill_contexts(records, request):
    """Read only authorized, original user sources; related history grants no skill."""
    sources = set(request.source_message_ids) | {r.source_message_id for r in request.revisions}
    selected = {}
    for record in records:
        if record.get("role") != "user" or record.get("message_id") not in sources:
            continue
        skills = record.get("skill_contexts", [])
        if not isinstance(skills, (list, tuple)):
            raise ValueError("技能说明列表无效，请重新选择技能。")
        for value in (*skills, record.get("skill_context")):
            skill = snapshot_skill_context(value)
            if skill is None:
                continue
            selected[skill["name"]] = skill
    return tuple(selected.values())


def skill_messages(records, request):
    return [
        {
            "role": "system",
            "content": f"User-selected skill for this request only: {skill['name']}\n{skill['prompt']}",
        }
        for skill in request_skill_contexts(records, request)
        if skill["prompt"]
    ]
