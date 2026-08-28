"""SessionManager — 多会话持久化管理。

ADR-008 (D13-D14): 后端纯 Python，零 PyQt6 依赖。
全局 data/sessions/ 目录，每个会话一个 JSON 文件。
启动时目录扫描加载元数据，消息列表懒加载。
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any
import uuid

logger = logging.getLogger(__name__)

_SESSIONS_DIR_NAME = "sessions"
_LEGACY_DEGRADATION_REASONS = (
    "backend_history_unavailable",
    "controller_state_unavailable",
    "owner_scope_unavailable",
    "task_refs_unavailable",
)


class SessionManager:
    """多会话 CRUD + JSON 持久化。

    元数据常驻内存，消息列表按需加载。
    """

    def __init__(self, data_dir: str | Path = ""):
        self._dir = Path(data_dir) / _SESSIONS_DIR_NAME
        self._dir.mkdir(parents=True, exist_ok=True)
        # 内存缓存：session_id → 元数据字典（不含 messages）
        self._cache: dict[str, dict] = {}
        self._scan()

    # ── 公开 API ─────────────────────────────────────────────

    def create_session(self, name: str = "", project_name: str = "") -> str:
        """创建新会话，返回 session_id。"""
        sid = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat()
        if not name:
            name = "新对话"
        meta = {
            "session_id": sid,
            "name": name,
            "created_at": now,
            "last_active_at": now,
            "project_name": project_name,
            "message_count": 0,
            "recovery": "degraded",
            "degradation_reasons": list(_LEGACY_DEGRADATION_REASONS),
            "persistence_format": "legacy-messages-only",
        }
        # 写入空会话文件
        data = dict(meta, messages=[])
        self._atomic_write(self._path_for(sid), data)
        self._cache[sid] = meta
        logger.info("SessionManager: 创建会话 %s (%s)", sid, name)
        return sid

    def delete_session(self, session_id: str) -> bool:
        """删除会话及其文件。返回是否成功。"""
        if session_id not in self._cache:
            return False
        try:
            path = self._path_for(session_id)
            if path.exists():
                path.unlink()
            del self._cache[session_id]
            logger.info("SessionManager: 删除会话 %s", session_id)
            return True
        except OSError:
            logger.warning("SessionManager: 删除会话文件失败 %s", session_id)
            return False

    def list_sessions(self) -> list[dict]:
        """返回所有会话元数据列表，按 last_active_at 降序。"""
        result = list(self._cache.values())
        result.sort(key=lambda m: (m.get("last_active_at", ""), m.get("created_at", "")), reverse=True)
        return result

    def get_session(self, session_id: str) -> dict | None:
        """加载完整会话数据（含消息列表）。更新 last_active_at。"""
        if session_id not in self._cache:
            return None
        path = self._path_for(session_id)
        if not path.exists():
            del self._cache[session_id]
            return None
        try:
            data = _mark_legacy_degraded(json.loads(path.read_text(encoding="utf-8")))
            # 更新活跃时间
            data["last_active_at"] = datetime.now().isoformat()
            self._cache[session_id] = {k: v for k, v in data.items() if k != "messages"}
            self._cache[session_id]["message_count"] = len(data.get("messages", []))
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("SessionManager: 读取会话 %s 失败: %s", session_id, exc)
            return None

    def save_session(self, session_id: str, messages: list[dict]) -> bool:
        """保存会话的消息列表。原子写入。"""
        if session_id not in self._cache:
            return False
        try:
            meta = self._cache[session_id]
            meta["last_active_at"] = datetime.now().isoformat()
            meta["message_count"] = len(messages)
            data = dict(meta, messages=messages)
            self._atomic_write(self._path_for(session_id), data)
            return True
        except OSError as exc:
            logger.warning("SessionManager: 保存会话 %s 失败: %s", session_id, exc)
            return False

    def rename_session(self, session_id: str, new_name: str) -> bool:
        """重命名会话。更新内存缓存 + 磁盘文件中的 name 字段。"""
        if session_id not in self._cache:
            return False
        path = self._path_for(session_id)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["name"] = new_name
            self._atomic_write(path, data)
            self._cache[session_id]["name"] = new_name
            return True
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("SessionManager: 重命名会话 %s 失败: %s", session_id, exc)
            return False

    def get_last_active(self) -> str | None:
        """返回最近活跃的会话 ID，无会话时返回 None。"""
        sessions = self.list_sessions()
        return sessions[0]["session_id"] if sessions else None

    def count(self) -> int:
        return len(self._cache)

    # ── 内部方法 ─────────────────────────────────────────────

    def _path_for(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"

    def _atomic_write(self, path: Path, data: dict) -> None:
        """原子写入：先写临时文件，再 os.replace。"""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _scan(self) -> None:
        """启动时扫描目录，加载所有会话元数据（不含消息列表）。"""
        try:
            for f in sorted(self._dir.glob("*.json")):
                try:
                    data = _mark_legacy_degraded(json.loads(f.read_text(encoding="utf-8")))
                    sid = data.get("session_id", f.stem)
                    self._cache[sid] = {
                        "session_id": sid,
                        "name": data.get("name", f.stem),
                        "created_at": data.get("created_at", ""),
                        "last_active_at": data.get("last_active_at", ""),
                        "project_name": data.get("project_name", ""),
                        "message_count": len(data.get("messages", [])),
                        "recovery": data["recovery"],
                        "degradation_reasons": data["degradation_reasons"],
                        "persistence_format": data["persistence_format"],
                    }
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning("SessionManager: 跳过损坏文件 %s: %s", f.name, exc)
        except OSError as exc:
            logger.warning("SessionManager: 扫描目录失败: %s", exc)
        logger.info("SessionManager: 已加载 %d 个会话", len(self._cache))


def _mark_legacy_degraded(data: dict[str, Any]) -> dict[str, Any]:
    """Never expose a messages-only facade record as a full Session recovery."""
    marked = dict(data)
    marked["recovery"] = "degraded"
    reasons = set(str(value) for value in marked.get("degradation_reasons", ()))
    reasons.update(_LEGACY_DEGRADATION_REASONS)
    marked["degradation_reasons"] = sorted(reasons)
    marked["persistence_format"] = "legacy-messages-only"
    return marked
