"""ParaTranz 工具基础测试 (C1-fix: 补全零覆盖缺口).

测试 9 个 PT 工具的基本行为：参数校验、错误路径、无网络时失败处理。
不测试实际网络调用。
"""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from tests.conftest import MockAppContext, make_test_collection
from transbridge.application.tasks import TaskCancelled

_PT_PROJECT_API_PATH = "transbridge.paratranz.service.ParaTranzService.from_config"


# ============================================================
# TestListProjects (3 cases)
# ============================================================
class TestListProjects(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_paratranz import _tool_list_projects

        self.func = _tool_list_projects
        self.ctx = MockAppContext()

    @patch(_PT_PROJECT_API_PATH)
    def test_list_my_projects_default_uid(self, mock_api):
        mock_client = MagicMock()
        mock_client.list_projects.return_value = [
            {"id": 1, "name": "TestProject", "visibility": "private"},
        ]
        mock_api.return_value = mock_client

        r = self.func({}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(r.data["projects"][0]["name"], "TestProject")

    @patch(_PT_PROJECT_API_PATH)
    def test_list_all_projects_empty_uid(self, mock_api):
        mock_client = MagicMock()
        mock_client.list_projects.return_value = []
        mock_api.return_value = mock_client

        r = self.func({"uid": ""}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(r.data["projects"], [])

    @patch(_PT_PROJECT_API_PATH)
    def test_list_projects_api_failure(self, mock_api):
        mock_api.side_effect = RuntimeError("API 不可用")

        r = self.func({}, self.ctx)
        self.assertFalse(r.success)
        self.assertIn("失败", r.message)


# ============================================================
# TestGetProjectInfo (2 cases)
# ============================================================
class TestGetProjectInfo(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_paratranz import _tool_get_project_info

        self.func = _tool_get_project_info
        self.ctx = MockAppContext()

    def test_get_project_info_no_project_id(self):
        r = self.func({}, self.ctx)
        self.assertFalse(r.success)
        self.assertIn("project_id", r.message)

    @patch(_PT_PROJECT_API_PATH)
    def test_invalid_explicit_project_id_does_not_fall_back_to_binding(self, mock_api):
        self.ctx.paratranz_binding = {
            "project_id": 42,
            "project_name": "Bound",
            "endpoint": "https://paratranz.cn",
        }

        for value in ("not-an-id", 1.5, False, -1):
            with self.subTest(project_id=value):
                r = self.func({"project_id": value}, self.ctx)
                self.assertFalse(r.success)
                self.assertIn("正整数", r.message)
        mock_api.assert_not_called()

    @patch(_PT_PROJECT_API_PATH)
    def test_endpoint_mismatch_blocks_bound_target_before_network(self, mock_api):
        self.ctx.config.base_url = "https://other.example/api"
        self.ctx.paratranz_binding = {
            "project_id": 42,
            "project_name": "Bound",
            "endpoint": "https://paratranz.cn",
        }

        r = self.func({}, self.ctx)

        self.assertFalse(r.success)
        self.assertIn("服务地址", r.message)
        mock_api.assert_not_called()

    @patch(_PT_PROJECT_API_PATH)
    def test_get_project_info_with_id(self, mock_api):
        mock_client = MagicMock()
        mock_client.get_project.return_value = {
            "id": 123,
            "name": "MyMod",
            "visibility": "public",
            "members": [1, 2],
        }
        mock_api.return_value = mock_client

        r = self.func({"project_id": 123}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(r.data["name"], "MyMod")
        self.assertEqual(r.data["member_count"], 2)
        mock_client.close.assert_called_once_with()

    @patch(_PT_PROJECT_API_PATH)
    def test_client_is_closed_when_project_lookup_fails(self, mock_api):
        mock_client = MagicMock()
        mock_client.get_project.side_effect = RuntimeError("offline")
        mock_api.return_value = mock_client

        r = self.func({"project_id": 123}, self.ctx)

        self.assertFalse(r.success)
        mock_client.close.assert_called_once_with()


# ============================================================
# TestCompareWithRemote (2 cases)
# NOTE: @require_collection 装饰器自动注入 collection，调用时只需 (args, ctx)
# ============================================================
class TestCompareWithRemote(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_paratranz import _tool_compare_with_remote

        self.func = _tool_compare_with_remote
        self.ctx = MockAppContext(make_test_collection(5))

    def test_compare_no_project_id(self):
        r = self.func({}, self.ctx)
        self.assertFalse(r.success)
        self.assertIn("project_id", r.message)

    @patch(_PT_PROJECT_API_PATH)
    def test_compare_all_local_only(self, mock_api):
        mock_client = MagicMock()
        mock_client.list_entries.return_value = []
        mock_api.return_value = mock_client

        r = self.func({"project_id": 1}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(r.data["only_local"], 5)


# ============================================================
# TestUploadEntries (2 cases)
# NOTE: @require_collection 装饰器自动注入 collection
# ============================================================
class TestUploadEntries(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_paratranz import _tool_upload_entries

        self.func = _tool_upload_entries
        self.ctx = MockAppContext(make_test_collection(3))

    def test_upload_no_project_id(self):
        r = self.func({}, self.ctx)
        self.assertFalse(r.success)

    @patch(_PT_PROJECT_API_PATH)
    def test_upload_all_entries(self, mock_api):
        mock_client = MagicMock()
        mock_client.upsert_entry.return_value = None
        mock_api.return_value = mock_client

        r = self.func({"project_id": 1}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(r.data["uploaded"], 3)


# ============================================================
# TestDownloadEntries (2 cases)
# ============================================================
class TestDownloadEntries(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_paratranz import _tool_download_entries

        self.func = _tool_download_entries
        self.ctx = MockAppContext(make_test_collection(3))

    def test_download_no_project_id(self):
        r = self.func({}, self.ctx)
        self.assertFalse(r.success)

    @patch(_PT_PROJECT_API_PATH)
    def test_download_entries(self, mock_api):
        mock_client = MagicMock()
        mock_client.list_entries.return_value = [
            {"key": "NPC_:0001", "original": "Hello", "translation": "你好", "context": "NPC_:FULL", "stage": 1},
        ]
        mock_api.return_value = mock_client

        r = self.func({"project_id": 1}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(r.data["downloaded_count"], 1)


# ============================================================
# TestExportArtifact (2 cases)
# ============================================================
class TestExportArtifact(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_paratranz import _tool_export_artifact

        self.func = _tool_export_artifact
        self.ctx = MockAppContext()

    def test_export_no_project_id(self):
        r = self.func({}, self.ctx)
        self.assertFalse(r.success)

    @patch(_PT_PROJECT_API_PATH)
    def test_export_artifact_success(self, mock_api):
        mock_service = MagicMock()
        mock_service.trigger_export.return_value = {"job_id": "abc"}
        mock_service.get_artifacts.return_value = [{"url": "https://example.com/artifact.zip"}]
        mock_api.return_value = mock_service

        r = self.func({"project_id": 1}, self.ctx)
        self.assertTrue(r.success)

    @patch(_PT_PROJECT_API_PATH)
    def test_export_cancellation_propagates(self, mock_api):
        mock_service = MagicMock()
        mock_service.trigger_export.side_effect = TaskCancelled("cancelled")
        mock_api.return_value = mock_service

        with self.assertRaises(TaskCancelled):
            self.func({"project_id": 1}, self.ctx)


# ============================================================
# TestGetUploadHistory (2 cases)
# ============================================================
class TestGetUploadHistory(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_paratranz import _tool_get_upload_history

        self.func = _tool_get_upload_history
        self.ctx = MockAppContext()

    def test_history_no_project_id(self):
        r = self.func({}, self.ctx)
        self.assertFalse(r.success)

    @patch(_PT_PROJECT_API_PATH)
    def test_history_with_project(self, mock_api):
        mock_client = MagicMock()
        mock_client.list_upload_history.return_value = [
            {"id": 1, "status": "success", "entries_count": 10},
        ]
        mock_api.return_value = mock_client

        r = self.func({"project_id": 1}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(len(r.data["history"]), 1)


# ============================================================
# TestGetParatranzProject (2 cases)
# ============================================================
class TestGetParatranzProject(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_paratranz import _tool_get_paratranz_project

        self.func = _tool_get_paratranz_project
        self.ctx = MockAppContext()

    def test_no_selected_project(self):
        r = self.func({}, self.ctx)
        self.assertTrue(r.success)
        self.assertIsNone(r.data["selected_project"])

    @patch(_PT_PROJECT_API_PATH)
    def test_with_selected_project(self, mock_api):
        self.ctx.paratranz_binding = {
            "project_id": 42,
            "project_name": "SkyrimCN",
            "endpoint": "https://paratranz.cn",
        }
        mock_client = MagicMock()
        mock_client.get_project.return_value = {
            "id": 42,
            "name": "SkyrimCN",
            "visibility": "public",
        }
        mock_api.return_value = mock_client

        r = self.func({}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(r.data["name"], "SkyrimCN")


# ============================================================
# TestSwitchParatranzProject (2 cases)
# ============================================================
class TestSwitchParatranzProject(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_paratranz import _tool_switch_paratranz_project

        self.func = _tool_switch_paratranz_project
        self.ctx = MockAppContext()

    @patch(_PT_PROJECT_API_PATH)
    def test_switch_project_success(self, mock_api):
        mock_client = MagicMock()
        mock_client.get_project.return_value = {
            "id": 99,
            "name": "TestMod",
            "visibility": "private",
        }
        mock_api.return_value = mock_client
        captured = []
        self.ctx.active_project_id = "local-project"
        self.ctx.set_paratranz_binding = lambda binding: (
            captured.append(binding) or SimpleNamespace(is_success=True, diagnostics=())
        )

        r = self.func({"project_id": 99}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(captured[0].project_id, 99)

    @patch(_PT_PROJECT_API_PATH)
    def test_switch_project_api_failure(self, mock_api):
        mock_api.side_effect = RuntimeError("项目不存在")

        r = self.func({"project_id": 999}, self.ctx)
        self.assertFalse(r.success)
        self.assertIn("失败", r.message)


class TestTranslatorParaTranzTarget(unittest.TestCase):
    def test_term_source_rejects_mismatched_binding_status(self):
        from transbridge.application.projects import ParaTranzTargetStatus
        from transbridge.smart_assistant.tools.tool_translator import _paratranz_term_project_id

        ctx = SimpleNamespace(
            resolve_paratranz_target=lambda: SimpleNamespace(
                project_id=42,
                status=ParaTranzTargetStatus.ACCOUNT_MISMATCH,
            )
        )

        self.assertIsNone(_paratranz_term_project_id(ctx))

    def test_term_source_accepts_available_binding_status(self):
        from transbridge.application.projects import ParaTranzTargetStatus
        from transbridge.smart_assistant.tools.tool_translator import _paratranz_term_project_id

        ctx = SimpleNamespace(
            resolve_paratranz_target=lambda: SimpleNamespace(
                project_id=42,
                status=ParaTranzTargetStatus.AVAILABLE,
            )
        )

        self.assertEqual(_paratranz_term_project_id(ctx), 42)


if __name__ == "__main__":
    unittest.main()
