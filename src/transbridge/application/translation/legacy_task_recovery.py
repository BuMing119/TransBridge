"""Conservative, read-only recovery discovery for reports predating manifests."""

from __future__ import annotations

import logging
from pathlib import Path

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY

from .task_candidate_recovery import checkpoint_identities, checkpoint_references, recovery_inputs
from .task_history import TaskHistoryStore
from .task_recovery import TaskRecoveryStore

logger = logging.getLogger(__name__)
_UNFINISHED = frozenset({
    "preparing",
    "running",
    "interrupted",
    "applying",
    "pending_confirmation",
    "partial",
    "failed",
})


def _source_inputs(source):
    snapshot = source.get("snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("entries"), list):
        raise ValueError("旧任务没有完整的原始范围报告。")
    rows = []
    for entry in snapshot["entries"]:
        try:
            rows.append({
                "entry_key": entry["entry_key"],
                "original": entry["original"],
                "translation": entry["before"],
                "stage": entry["stage"],
                "context": entry.get("context", ""),
                "revision": entry["before_revision"],
            })
        except (KeyError, TypeError) as exc:
            raise ValueError("旧任务缺少原始输入快照。") from exc
    parsed = recovery_inputs({"inputs": rows})
    # Reports do not retain original translate/polish/custom action partitions.
    # A wholly untranslated input plus translation checkpoints is unambiguous;
    # never guess that an older mixed/polish input should be overwritten.
    if not parsed or any(
        entry.translation.strip()
        or not entry.original.strip()
        or not DEFAULT_STAGE_POLICY.allows_ai(entry.stage, entry.translation, original=entry.original)
        for entry in parsed.values()
    ):
        raise ValueError("旧任务没有原动作分区，无法可靠确定翻译范围。")
    return rows


def discover_legacy_records(project_directory, *, runtime_root=None) -> tuple[dict, ...]:
    """Build virtual manifests; neither reports nor checkpoints are rewritten.

    Full candidate validation occurs only when the user continues a task. Old
    execution settings are unavailable, so these records explicitly request a
    new recovery chain with current settings and another quality review.
    """
    directory = Path(project_directory)
    history = TaskHistoryStore(directory / "ai-task-history")
    known = {record["task_id"] for record in TaskRecoveryStore(directory / "ai-task-recovery").list_records()}
    pending = tuple(
        record
        for record in history.list_records()
        if record.get("run_id") not in known
        and record.get("state") in _UNFINISHED
        and record.get("project_id")
        and record.get("variant_id")
        and not record.get("project_saved", record.get("saved", False))
    )
    if not pending:
        return ()
    if runtime_root is None:
        from transbridge.config.paths import get_data_dir

        runtime_root = Path(get_data_dir()) / "ai_translator"
    identities = tuple(
        identity
        for root in sorted(Path(runtime_root).glob("*/runtime-v2"))
        for identity in checkpoint_identities(root, strict=False)
    )
    records = []
    for metadata in pending:
        run_id = metadata["run_id"]
        try:
            report = history.load(run_id)
            sources = []
            seen = set()
            for source in report["sources"]:
                if source["key"] in seen:
                    raise ValueError("旧任务包含重复来源。")
                seen.add(source["key"])
                inputs = _source_inputs(source)
                references = tuple(
                    checkpoint_references(
                        Path(runtime_root), (run_id,), source["key"], strict=False, identities=identities
                    )
                )
                if not references:
                    raise ValueError("旧任务缺少可归属的翻译检查点，无法确认原任务动作。")
                sources.append({
                    "key": source["key"],
                    "label": source["label"],
                    "translate_keys": [row["entry_key"] for row in inputs],
                    "polish_keys": [],
                    "inputs": inputs,
                    "checkpoint_stems": sorted({reference.path.parent.parent.name for reference in references}),
                })
            if not sources:
                continue
            keys = [EntryKey.from_dict(key) for source in sources for key in source["translate_keys"]]
            if len(keys) != len(set(keys)):
                raise ValueError("旧任务的多个来源包含重复词条身份。")
        except (KeyError, TypeError, ValueError) as exc:
            logger.info("Legacy AI task %s remains view-only: %s", run_id, exc)
            continue
        records.append({
            "task_id": run_id,
            "created_at": report["created_at"],
            "project_id": report["project_id"],
            "variant_id": report["variant_id"],
            "mode": "translate",
            "state": "interrupted",
            "supported": True,
            "legacy": True,
            "config_digest": "legacy-current-settings",
            "config_policy": "current-settings",
            "execution_runs": [run_id],
            "origin_task_id": run_id,
            "sources": sources,
        })
    return tuple(records)
