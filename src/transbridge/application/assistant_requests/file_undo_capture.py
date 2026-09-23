"""File-domain capture with durable Session intents and application-owned backups.

Lock order is Session then FILE_UNDO_LOCK. Callers run this off the UI thread.
The lock serializes cooperating assistant publishers only, not external editors.
"""

from pathlib import Path
from threading import RLock
from uuid import uuid4

from transbridge.application.io.file_undo import FileUndoAdapter

from .admission import validate_effect
from .models import RequestError
from .round_undo import _execution_owner

FILE_UNDO_LOCK = RLock()


def capture_file_commit(undo, context, execution, effect_id, targets, mutation, *, backup_root):
    """Targets must already be authorized by the application's path policy."""
    with undo.requests.serialized(context), FILE_UNDO_LOCK:
        owner = _execution_owner(undo.requests.state(context), context, execution)
        validate_effect(owner, effect_id)
        adapter = FileUndoAdapter(targets, backup_root=backup_root)
        before = adapter.capture_before()
        reference = undo._store(context, before)
        identity = uuid4().hex

        def begin(state):
            request = _execution_owner(state, context, execution)
            record = state.get("undo_rounds", {}).get(execution.work_round_id)
            if (
                record is None
                or record["status"] != "recording"
                or effect_id not in record["effects"]
                or not any(e.effect_id == effect_id and e.execution == execution for e in request.effects)
            ):
                raise RequestError("UNDO_ROUND_CLOSED", "文件修改没有有效的本轮撤销归属")
            record["commits"].append({
                "id": identity,
                "effect_id": effect_id,
                "kind": "files",
                "status": "pending",
                "targets": [str(path) for path in adapter.targets],
                "before": reference.to_dict(),
            })

        undo.requests.transact(context, begin, artifact_refs=(reference,))
        # An exception may follow a partial publication: keep the intent pending.
        result = mutation()
        receipt = adapter.capture_after(before)
        sealed = undo._store(context, receipt)
        undo._finish(context, execution.work_round_id, identity, "recorded", receipt_ref=sealed)
        return result


def _adapters(undo, context, commits, backup_root):
    seen = set()
    pairs = []
    for commit in commits:
        if commit.get("kind") != "files" or commit["status"] != "recorded":
            raise RequestError("UNDO_FILE_RECEIPT_INVALID", "文件撤销需要已确认的文件凭证")
        targets = tuple(Path(value) for value in commit["targets"])
        if seen.intersection(targets):
            raise RequestError("UNDO_FILE_OVERLAP", "本轮多次写入同一文件，尚不能安全合并逆操作")
        seen.update(targets)
        pairs.append((FileUndoAdapter(targets, backup_root=backup_root), undo._read(context, commit["receipt"])))
    return pairs


def preflight_file_commits(undo, context, commits, *, backup_root):
    """All-file preflight; receipt targets cannot expand the trusted commit list."""
    with FILE_UNDO_LOCK:
        errors = [
            error
            for adapter, receipt in _adapters(undo, context, commits, backup_root)
            for error in adapter.preflight(receipt)
        ]
        if errors:
            raise RequestError("UNDO_FILE_CONFLICT", "; ".join(f"{e['path']}: {e['error']}" for e in errors))


def apply_file_commits(undo, context, commits, *, backup_root):
    """Validate the whole set before touching files; report any partial progress."""
    with FILE_UNDO_LOCK:
        pairs = _adapters(undo, context, commits, backup_root)
        errors = [error for adapter, receipt in pairs for error in adapter.preflight(receipt)]
        remaining = [str(path) for adapter, _ in pairs for path in adapter.targets]
        if errors:
            return {"status": "conflict", "restored": [], "remaining": remaining, "errors": errors}
        restored = []
        for adapter, receipt in pairs:
            result = adapter.apply(receipt)
            restored.extend(result["restored"])
            remaining = [path for path in remaining if path not in result["restored"]]
            if result["status"] != "completed":
                return {
                    **result,
                    "status": "partial" if restored else result["status"],
                    "restored": restored,
                    "remaining": remaining,
                }
        return {"status": "completed", "restored": restored, "remaining": [], "errors": []}
