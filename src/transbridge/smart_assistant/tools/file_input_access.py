# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Resolve assistant file inputs and discovered plugin resources against request grants."""

from pathlib import Path

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.security.paths import PathAuthorizationPolicy, PathGrant
from transbridge.parser.localized_resources import _case_child, candidate_input_paths


def require_input_access(grant, path, base=None):
    decision = grant.authorize(path, working_directory=base)
    if not decision.allowed:
        raise RequestError(decision.code, decision.reason)


def authorize_plugin_resources(grant, path, language="english"):
    require_input_access(grant, path.parent)
    strings = _case_child(path.parent, "Strings")
    if strings.exists():
        require_input_access(grant, strings)
    for companion in candidate_input_paths(path, language):
        if companion.exists():
            require_input_access(grant, companion)


def authorized_input_path(ctx, path, *, plugin=False) -> Path:
    request = getattr(ctx, "request_context", None) or getattr(ctx, "runtime_context", None)
    roots = getattr(request, "authorized_roots", ()) or getattr(ctx, "authorized_roots", ())
    if not roots:
        raise RequestError("PATH_GRANT_REQUIRED", "读取文件缺少应用授权目录。")
    base = Path(dict(getattr(request, "metadata", ())).get("working_directory") or roots[0])
    grant = PathAuthorizationPolicy(PathGrant(Path(root)) for root in roots)
    require_input_access(grant, path, base)
    resolved = (base / path).resolve(strict=True)
    if plugin:
        authorize_plugin_resources(grant, resolved)
    return resolved
