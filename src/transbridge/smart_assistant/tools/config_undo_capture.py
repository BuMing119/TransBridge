"""Save only the settings actually changed by an admitted assistant command."""

from transbridge.application.assistant_requests.config_undo_capture import capture_config_commit
from transbridge.application.assistant_requests.models import RequestError

from .undo_admission import assistant_undo_target


def save_config(context, config, fields):
    target = assistant_undo_target(context)
    if target is None:
        config.save_to_file()
        return
    gate, undo, effect = target
    if str(config._repository.path) != str(undo.configuration().path):
        raise RequestError("UNDO_SCOPE_MISMATCH", "配置保存目标与撤销服务不一致")
    updates = {key: getattr(config, key) for key in fields}
    if "term_priority" in updates:
        updates["term_priority"] = ",".join(updates["term_priority"])
    after = capture_config_commit(undo, gate.context, effect.execution, effect.effect_id, updates)
    config.config_revision = after.revision
