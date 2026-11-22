# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Save only the settings actually changed by an admitted assistant command."""

from transbridge.application.assistant_requests.config_undo_capture import capture_config_commit
from transbridge.application.assistant_requests.models import RequestError

from .undo_admission import assistant_undo_target


def save_config(context, config, fields):
    target = assistant_undo_target(context)
    updates = {key: getattr(config, key) for key in fields}
    if "term_priority" in updates:
        updates["term_priority"] = ",".join(updates["term_priority"])
    if target is None:
        after = config._repository.update_sections({"llm": updates}, expected_revision=config.config_revision)
    else:
        gate, undo, effect = target
        if str(config._repository.path) != str(undo.configuration().path):
            raise RequestError("UNDO_SCOPE_MISMATCH", "配置保存目标与撤销服务不一致")
        after = capture_config_commit(
            undo, gate.context, effect.execution, effect.effect_id, updates, expected_revision=config.config_revision
        )
    config.config_revision = after.revision
