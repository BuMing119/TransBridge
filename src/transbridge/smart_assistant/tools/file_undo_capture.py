"""Authorize exact local output files before attaching assistant undo evidence."""

import os
from pathlib import Path

from transbridge.application.assistant_requests.file_undo_capture import FILE_UNDO_LOCK, capture_file_commit
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.security.paths import PathAuthorizationPolicy, PathGrant

from .undo_admission import assistant_undo_target


def authorize_file_outputs(context, targets):
    request = getattr(context, "request_context", None) or getattr(context, "runtime_context", None)
    roots = tuple(getattr(request, "authorized_roots", ()) or getattr(context, "authorized_roots", ()) or ())
    if not roots:
        raise RequestError("PATH_GRANT_REQUIRED", "文件修改缺少应用授权目录")
    metadata = dict(getattr(request, "metadata", ()) or ())
    base = Path(metadata.get("working_directory") or roots[0])
    policy = PathAuthorizationPolicy(PathGrant(Path(root), allow_create=True) for root in roots)
    output = []
    for value in targets:
        raw = Path(value)
        path = Path(os.path.abspath(raw if raw.is_absolute() else base / raw))
        # A generated companion may have a missing parent; authorize its nearest
        # existing ancestor without silently resolving away a link in the path.
        candidate = path
        while not candidate.parent.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        decision = policy.authorize(candidate, working_directory=base, for_creation=True)
        if not decision.allowed:
            raise RequestError(decision.code, decision.reason)
        output.append(path)
    return tuple(output)


def capture_file_command(context, targets, mutation):
    """Program-derived targets only; pass the same paths to the actual publisher."""
    targets = authorize_file_outputs(context, targets)
    target = assistant_undo_target(context)
    if target is None:
        with FILE_UNDO_LOCK:
            return mutation()
    gate, undo, effect = target
    if getattr(undo, "file_backup_root", None) is None:
        raise RequestError("UNDO_STORAGE_UNAVAILABLE", "文件撤销备份存储未配置，操作未开始")
    return capture_file_commit(
        undo,
        gate.context,
        effect.execution,
        effect.effect_id,
        targets,
        mutation,
        backup_root=undo.file_backup_root,
    )
