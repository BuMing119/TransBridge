# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""The current Variant's cache action and availability."""

from .action_catalog import IntentId


def _preview(host):
    return getattr(getattr(getattr(host, "workbench", None), "preview", None), "terminology_preview", None)


def _available(host):
    context = getattr(host, "context", None)
    enabled = bool(
        context is not None
        and getattr(context, "active_version_identity", None)
        and not getattr(context, "project_opening", False)
        and not getattr(context, "project_view_failed", False)
        and _preview(host) is not None
    )
    return enabled, None if enabled else "请先打开一个可用的本地翻译版本"


def register_alignment_cache_action(router, host):
    router.register(
        IntentId.SETTINGS_ALIGNMENT_CACHE_CLEAR,
        lambda _payload: _preview(host).clear_alignment_cache(),
        availability=lambda: _available(host),
    )
