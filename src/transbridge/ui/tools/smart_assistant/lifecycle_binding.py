from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def close_runtime_resources(
    *,
    observability,
    memory_store,
    orchestrator,
    wait_for_worker: bool,
) -> None:
    """Release chat-owned runtime adapters without touching application TaskRuntime."""
    try:
        if orchestrator is not None:
            orchestrator.shutdown(wait=wait_for_worker, timeout=3.0)
    except Exception:
        logger.debug("shutdown: 关闭 orchestrator 失败", exc_info=True)
    try:
        if observability is not None:
            observability.end_conversation()
            observability._on_token_stats_updated = None
    except Exception:
        logger.debug("shutdown: 清理 observability 失败", exc_info=True)
    try:
        if memory_store is not None:
            memory_store.close()
    except Exception:
        logger.debug("shutdown: 关闭 memory_store 失败", exc_info=True)


__all__ = ["close_runtime_resources"]
