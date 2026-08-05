from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class CheckpointManager:
    """图执行检查点管理器。负责 checkpoint 的保存、加载与路径管理。

    从 ExecutionEngine 提取，ADR-008 上帝类拆分 Story 01。
    """

    _SAFE_SERIALIZE_MAX_CHARS = 2000

    def __init__(self, checkpoint_dir: Path) -> None:
        self._checkpoint_dir = Path(checkpoint_dir)

    # ── 公开 API ────────────────────────────────────────────────

    def save_checkpoint(self, graph_id: str, current_node_id: str,
                        state: dict) -> None:
        """保存图执行的 checkpoint。自动创建目标目录。"""
        try:
            from .graph_types import Checkpoint
            serialized = {}
            for nid, r in state.items():
                serialized[nid] = {
                    "step_id": r.step_id, "tool": r.tool,
                    "success": r.success, "message": r.message,
                    "data": CheckpointManager._safe_serialize(r.data),
                    "duration_ms": r.duration_ms,
                }
            ckpt = Checkpoint(
                graph_id=graph_id, current_node_id=current_node_id,
                completed_results=serialized, graph_state={},
            )
            path = self.checkpoint_path(graph_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(ckpt.to_dict(), ensure_ascii=False, indent=2))
        except Exception as exc:
            logger.warning("Checkpoint 保存失败 (graph_id=%s): %s", graph_id, exc)

    def load_checkpoint(self, graph_id: str):
        """加载图执行的 checkpoint。损坏的 JSON 返回 None。"""
        try:
            from .graph_types import Checkpoint
            path = self.checkpoint_path(graph_id)
            if not path.exists():
                return None
            data = json.loads(path.read_text())
            return Checkpoint.from_dict(data)
        except Exception as exc:
            logger.warning("Checkpoint 加载失败 (graph_id=%s): %s", graph_id, exc)
            return None

    def checkpoint_path(self, graph_id: str) -> Path:
        """生成安全的 checkpoint 文件路径。使用正则白名单消毒 graph_id。"""
        safe_id = re.sub(r'[^a-zA-Z0-9_.-]', '_', graph_id)
        return self._checkpoint_dir / f"{safe_id}.json"

    @staticmethod
    def _safe_serialize(value):
        """仅允许 JSON 可序列化类型。不可序列化对象返回 None。"""
        if value is None:
            return None
        if isinstance(value, (dict, list, str, int, float, bool)):
            return value
        # m11: 避免对 Qt 对象调用 str() 泄露内存地址
        try:
            from PyQt6.QtCore import QObject
            if isinstance(value, QObject):
                return None
        except ImportError:
            pass
        return str(value)[:CheckpointManager._SAFE_SERIALIZE_MAX_CHARS]
