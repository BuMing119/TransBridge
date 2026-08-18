"""Compatibility facade over the versioned application checkpoint port."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from transbridge.application.tasks import (
    CheckpointExpectation,
    CheckpointFrontier,
    CheckpointRecord,
    FilesystemCheckpointPort,
    OwnerRef,
)


class CheckpointManager:
    """Graph-facing adapter; new recovery always uses the versioned envelope."""

    _SAFE_SERIALIZE_MAX_CHARS = 2000

    def __init__(self, checkpoint_dir: Path, *, fault_injector=None) -> None:
        self._checkpoint_dir = Path(checkpoint_dir)
        self._port = FilesystemCheckpointPort(self._checkpoint_dir / "v2", fault_injector=fault_injector)

    def save_record(self, record: CheckpointRecord) -> None:
        self._port.save(record)

    def load_record(
        self,
        run_id: str,
        *,
        expected: CheckpointExpectation,
    ) -> CheckpointRecord | None:
        return self._port.load(run_id, expected=expected)

    def delete_record(self, run_id: str) -> bool:
        return self._port.delete(run_id)

    def save_checkpoint(self, graph_id: str, current_node_id: str, state: dict) -> None:
        """Legacy call-shape writer using an explicit compatibility identity."""

        run_id = f"legacy-graph:{graph_id}"
        owner = OwnerRef("legacy-graph", "graph")
        serialized = tuple(sorted((node_id, self.serialize_step_result(result)) for node_id, result in state.items()))
        record = CheckpointRecord(
            run_id=run_id,
            owner=owner,
            spec_fingerprint=f"legacy:{graph_id}",
            input_fingerprint=f"legacy:{graph_id}",
            revision=0,
            frontier=CheckpointFrontier(
                ready=(current_node_id,) if current_node_id else (),
                completed=tuple(sorted(state)),
            ),
            completed_commit_ids=frozenset(state),
            graph_results=serialized,
        )
        self.save_record(record)

    def load_checkpoint(self, graph_id: str):
        """Legacy call-shape reader for checkpoints written by this facade."""

        from .graph_types import Checkpoint

        run_id = f"legacy-graph:{graph_id}"
        owner = OwnerRef("legacy-graph", "graph")
        expected = CheckpointExpectation(run_id, owner, f"legacy:{graph_id}", f"legacy:{graph_id}")
        record = self.load_record(run_id, expected=expected)
        if record is None:
            return None
        return Checkpoint(
            graph_id=graph_id,
            current_node_id=record.frontier.ready[0] if record.frontier.ready else "",
            completed_results={key: json.loads(value) for key, value in record.graph_results},
            graph_state={"schema_version": record.schema_version},
        )

    def load_legacy_checkpoint(self, graph_id: str):
        """Read a pre-V2 non-atomic checkpoint for inspection only.

        Callers must not use this result for resume because it has no owner/spec/input
        envelope and therefore cannot pass the V2 identity gate.
        """

        from .graph_types import Checkpoint

        path = self._legacy_checkpoint_path(graph_id)
        if not path.exists():
            return None
        return Checkpoint.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def checkpoint_path(self, graph_id: str) -> Path:
        return self._port.path_for(f"legacy-graph:{graph_id}")

    def inject(self, stage: str, run_id: str) -> None:
        self._port._inject(stage, self._port.path_for(run_id))

    def _legacy_checkpoint_path(self, graph_id: str) -> Path:
        safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", graph_id)
        return self._checkpoint_dir / f"{safe_id}.json"

    @classmethod
    def serialize_step_result(cls, result) -> dict[str, Any]:
        return {
            "step_id": int(result.step_id),
            "tool": str(result.tool),
            "success": bool(result.success),
            "message": str(result.message),
            "data": cls._safe_serialize(result.data),
            "duration_ms": int(result.duration_ms),
            "agent_instance_id": str(getattr(result, "agent_instance_id", "")),
        }

    @staticmethod
    def _safe_serialize(value, *, _depth: int = 0):
        if _depth > 20:
            return None
        if value is None or isinstance(value, str | int | bool):
            return value
        if isinstance(value, float):
            return value if value == value and value not in {float("inf"), float("-inf")} else None
        if isinstance(value, list | tuple):
            return [CheckpointManager._safe_serialize(item, _depth=_depth + 1) for item in value]
        if isinstance(value, dict):
            return {str(key): CheckpointManager._safe_serialize(item, _depth=_depth + 1) for key, item in value.items()}
        return str(value)[: CheckpointManager._SAFE_SERIALIZE_MAX_CHARS]
