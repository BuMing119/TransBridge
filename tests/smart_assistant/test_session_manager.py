"""SessionManager 单元测试 — 覆盖 CRUD、持久化、异常处理。

FR13 Story 01: SessionManager 后端 + ConversationManager 序列化。
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pytest

from transbridge.smart_assistant.conversation_manager import ConversationManager
from transbridge.smart_assistant.session_manager import SessionManager


class TestSessionManagerCRUD:
    """基本 CRUD 操作测试。"""

    @pytest.fixture
    def tmp_dir(self):
        with tempfile.TemporaryDirectory() as d:
            yield d

    @pytest.fixture
    def mgr(self, tmp_dir):
        return SessionManager(tmp_dir)

    def test_create_session_returns_id(self, mgr):
        sid = mgr.create_session("测试会话")
        assert len(sid) == 12
        assert mgr.count() == 1

    def test_create_session_auto_name(self, mgr):
        mgr.create_session()
        sessions = mgr.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["name"] == "新对话"

    def test_create_session_persists_file(self, mgr, tmp_dir):
        sid = mgr.create_session("持久化测试")
        path = Path(tmp_dir) / "sessions" / f"{sid}.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["name"] == "持久化测试"
        assert data["messages"] == []

    def test_list_sessions_sorted_by_active(self, mgr):
        import time
        sid1 = mgr.create_session("旧会话")
        time.sleep(0.01)  # 确保时间戳不同
        sid2 = mgr.create_session("新会话")
        sessions = mgr.list_sessions()
        # 最新的在前
        assert sessions[0]["session_id"] == sid2
        assert sessions[1]["session_id"] == sid1

    def test_get_session_returns_full_data(self, mgr):
        sid = mgr.create_session("完整测试", "TestProject")
        mgr.save_session(sid, [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ])
        data = mgr.get_session(sid)
        assert data is not None
        assert data["name"] == "完整测试"
        assert data["project_name"] == "TestProject"
        assert len(data["messages"]) == 2
        assert data["recovery"] == "degraded"
        assert data["persistence_format"] == "legacy-messages-only"
        assert "backend_history_unavailable" in data["degradation_reasons"]

    def test_get_session_updates_last_active(self, mgr):
        sid = mgr.create_session("活跃测试")
        old_meta = mgr.list_sessions()[0]
        old_active = old_meta["last_active_at"]
        mgr.get_session(sid)
        new_meta = mgr.list_sessions()[0]
        assert new_meta["last_active_at"] >= old_active

    def test_get_session_nonexistent(self, mgr):
        assert mgr.get_session("nonexistent") is None

    def test_delete_session_removes_from_cache(self, mgr):
        sid = mgr.create_session("待删除")
        assert mgr.count() == 1
        assert mgr.delete_session(sid) is True
        assert mgr.count() == 0

    def test_delete_session_removes_file(self, mgr, tmp_dir):
        sid = mgr.create_session("文件删除测试")
        path = Path(tmp_dir) / "sessions" / f"{sid}.json"
        assert path.exists()
        mgr.delete_session(sid)
        assert not path.exists()

    def test_delete_session_nonexistent(self, mgr):
        assert mgr.delete_session("nonexistent") is False

    def test_save_session_updates_message_count(self, mgr):
        sid = mgr.create_session("计数测试")
        mgr.save_session(sid, [{"role": "user", "content": "msg"}] * 5)
        meta = mgr.list_sessions()[0]
        assert meta["message_count"] == 5

    def test_save_session_nonexistent(self, mgr):
        assert mgr.save_session("nonexistent", []) is False

    def test_get_last_active_returns_most_recent(self, mgr):
        import time
        mgr.create_session("旧")
        time.sleep(0.01)
        sid2 = mgr.create_session("新")
        assert mgr.get_last_active() == sid2

    def test_get_last_active_empty(self, mgr):
        assert mgr.get_last_active() is None


class TestSessionManagerPersistence:
    """持久化与恢复测试。"""

    @pytest.fixture
    def tmp_dir(self):
        with tempfile.TemporaryDirectory() as d:
            yield d

    def test_scan_restores_sessions(self, tmp_dir):
        # 第一次创建
        mgr1 = SessionManager(tmp_dir)
        sid = mgr1.create_session("恢复测试", "MyProject")
        mgr1.save_session(sid, [{"role": "user", "content": "persist me"}])

        # 第二次创建，验证扫描恢复
        mgr2 = SessionManager(tmp_dir)
        assert mgr2.count() == 1
        sessions = mgr2.list_sessions()
        assert sessions[0]["name"] == "恢复测试"
        assert sessions[0]["project_name"] == "MyProject"
        assert sessions[0]["message_count"] == 1

    def test_scan_lazy_loads_messages(self, tmp_dir):
        mgr1 = SessionManager(tmp_dir)
        sid = mgr1.create_session("懒加载测试")
        mgr1.save_session(sid, [{"role": "user", "content": "lazy"}])

        mgr2 = SessionManager(tmp_dir)
        # 元数据已加载，但消息列表不在缓存中
        meta = mgr2.list_sessions()[0]
        assert "messages" not in meta
        # 调用 get_session 时才加载完整数据
        data = mgr2.get_session(sid)
        assert len(data["messages"]) == 1

    def test_corrupted_json_skipped(self, tmp_dir):
        # 手动写入损坏文件
        sessions_dir = Path(tmp_dir) / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / "bad.json").write_text("not valid json{{{", encoding="utf-8")
        # 正常文件
        good_data = {
            "session_id": "good123",
            "name": "正常会话",
            "created_at": "2026-01-01T00:00:00",
            "last_active_at": "2026-01-01T00:00:00",
            "project_name": "",
            "message_count": 0,
            "messages": [],
        }
        (sessions_dir / "good123.json").write_text(
            json.dumps(good_data, ensure_ascii=False), encoding="utf-8")

        mgr = SessionManager(tmp_dir)
        # 损坏文件被跳过，正常文件被加载
        assert mgr.count() == 1
        assert mgr.list_sessions()[0]["session_id"] == "good123"

    def test_missing_session_id_uses_filename(self, tmp_dir):
        sessions_dir = Path(tmp_dir) / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "name": "无ID会话",
            "created_at": "",
            "last_active_at": "",
            "project_name": "",
            "message_count": 0,
            "messages": [],
        }
        (sessions_dir / "fallback.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")

        mgr = SessionManager(tmp_dir)
        assert mgr.count() == 1
        assert mgr.list_sessions()[0]["session_id"] == "fallback"


class TestConversationManagerSerialization:
    """ConversationManager to_dict / from_dict 测试。"""

    def test_to_dict_returns_messages(self):
        conv = ConversationManager()
        conv.add_system("system prompt")
        conv.add_user("hello")
        conv.add_assistant("hi there")
        result = conv.to_dict()
        assert "messages" in result
        assert len(result["messages"]) == 3

    def test_to_dict_preserves_roles(self):
        conv = ConversationManager()
        conv.add_user("u")
        conv.add_assistant("a")
        result = conv.to_dict()
        roles = [m["role"] for m in result["messages"]]
        assert roles == ["user", "assistant"]

    def test_from_dict_replaces_messages(self):
        conv = ConversationManager()
        conv.add_user("old")
        conv.from_dict({"messages": [
            {"role": "system", "content": "new sys"},
            {"role": "user", "content": "new user"},
        ]})
        msgs = conv.get_messages()
        assert len(msgs) == 2
        assert msgs[0]["content"] == "new sys"
        assert msgs[1]["content"] == "new user"

    def test_from_dict_empty(self):
        conv = ConversationManager()
        conv.add_user("old")
        conv.from_dict({})
        assert len(conv.get_messages()) == 0

    def test_from_dict_rebuilds_turn_starts(self):
        conv = ConversationManager()
        conv.from_dict({"messages": [
            {"role": "user", "content": "turn1"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "turn2"},
            {"role": "assistant", "content": "resp2"},
        ]})
        # turn_starts 应该是 [0, 2]
        assert len(conv._turn_starts) == 2
        assert conv._turn_starts[0] == 0
        assert conv._turn_starts[1] == 2

    def test_roundtrip(self):
        conv1 = ConversationManager()
        conv1.add_system("sys")
        conv1.add_user("你好")
        conv1.add_assistant("你好！")
        conv1.add_user("帮我翻译")
        conv1.add_assistant("好的")

        data = conv1.to_dict()
        conv2 = ConversationManager()
        conv2.from_dict(data)

        assert conv2.get_messages() == conv1.get_messages()
