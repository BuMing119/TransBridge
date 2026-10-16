"""Project/Variant and task ownership checks shared by terminology commands."""

from transbridge.application.contracts import RequestContext
from transbridge.application.tasks import OwnerRef


def require_line(context: RequestContext) -> tuple[str, str]:
    if context.project_id is None or context.variant_id is None:
        raise RuntimeError("术语操作需要当前工程和翻译版本")
    return context.project_id, context.variant_id


def task_owner(context: RequestContext) -> OwnerRef:
    project_id, variant_id = require_line(context)
    return OwnerRef(
        context.owner_id,
        dict(context.metadata).get("entrypoint", "gui"),
        project_id=project_id,
        variant_id=variant_id,
        permissions=context.permissions,
    )
