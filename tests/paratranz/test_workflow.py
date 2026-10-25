"""
WorkFlow 单元测试：ParaTranzUploader / ArtifactWorkflow

所有测试均 mock API 层，不发出真实 HTTP 请求。
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import zipfile

import pytest

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.config_manager import ParatranzConfig
from transbridge.paratranz.workflow.artifact import ArtifactWorkflow
from transbridge.paratranz.workflow.uploader import ParaTranzUploader

# ─────────────────────────────────────────────────────────────
# 测试辅助函数
# ─────────────────────────────────────────────────────────────


def make_config() -> ParatranzConfig:
    return ParatranzConfig(token="test_token")


def make_entry(
    entry_id: str,
    original: str = "original",
    translation: str = "",
    stage: int = 0,
    context: str = "NPC_:FULL",
) -> TranslationEntry:
    return TranslationEntry(
        id=entry_id,
        key=entry_id,
        original=original,
        translation=translation,
        stage=stage,
        context=context,
    )


def make_collection(*entries: TranslationEntry) -> TranslationEntryCollection:
    return TranslationEntryCollection(entries=entries)


# ─────────────────────────────────────────────────────────────
# ParaTranzUploader
# ─────────────────────────────────────────────────────────────


class TestParaTranzUploader:
    def _make_uploader(self) -> tuple[ParaTranzUploader, MagicMock]:
        uploader = ParaTranzUploader(make_config())
        mock_api = MagicMock()
        uploader._api = mock_api
        return uploader, mock_api

    def test_single_upload_new_file(self):
        uploader, api = self._make_uploader()
        api.list_files.return_value = []
        payloads = []
        api.upload_file.side_effect = lambda project_id, path: payloads.append(
            json.loads(Path(path).read_text(encoding="utf-8"))
        )

        result = uploader.upload_collection_as_single(make_collection(make_entry("entry")), 1, "plugin.json")

        assert result.created == 1 and result.updated == result.skipped == 0
        assert result.files == ["plugin.json"]
        assert payloads[0][0]["key"] == "entry"
        api.reupload_file.assert_not_called()

    def test_single_upload_updates_existing_root_file(self):
        uploader, api = self._make_uploader()
        api.list_files.return_value = [{"id": 101, "name": "plugin.json"}]
        api.get_file_info.return_value = {"id": 101, "name": "plugin.json"}

        result = uploader.upload_collection_as_single(make_collection(make_entry("entry")), 1, "plugin.json")

        assert result.updated == 1 and result.created == result.skipped == 0
        assert result.files == ["plugin.json"]
        api.reupload_file.assert_called_once()
        assert api.reupload_file.call_args.args[:2] == (1, 101)
        api.upload_file.assert_not_called()

    def test_single_upload_error_keeps_filename_and_cause(self):
        uploader, api = self._make_uploader()
        api.list_files.return_value = []
        failure = RuntimeError("server error")
        api.upload_file.side_effect = failure

        with pytest.raises(RuntimeError, match="plugin.json.*server error") as error:
            uploader.upload_collection_as_single(make_collection(make_entry("entry")), 1, "plugin.json")

        assert error.value.__cause__ is failure
        api.upload_file.assert_called_once()

    def test_single_upload_progress_callback(self):
        uploader, api = self._make_uploader()
        api.list_files.return_value = []
        calls = []

        uploader.upload_collection_as_single(
            make_collection(make_entry("entry")),
            1,
            "plugin.json",
            progress_callback=lambda current, total, name: calls.append((current, total, name)),
        )

        assert calls[0][:2] == (0, 1)
        assert calls[-1] == (1, 1, "完成")

    def test_single_upload_raises_when_list_files_fails(self):
        uploader, api = self._make_uploader()
        failure = RuntimeError("network error")
        api.list_files.side_effect = failure

        with pytest.raises(RuntimeError, match="获取项目文件列表失败") as error:
            uploader.upload_collection_as_single(make_collection(make_entry("entry")), 1, "plugin.json")

        assert error.value.__cause__ is failure
        api.upload_file.assert_not_called()


# ─────────────────────────────────────────────────────────────
# ArtifactWorkflow
# ─────────────────────────────────────────────────────────────


class TestArtifactWorkflow:
    @staticmethod
    def _write_artifact(project_id, save_path):
        with zipfile.ZipFile(save_path, "w") as zf:
            zf.writestr("result.json", "{}")
        return save_path

    def _make_workflow(self) -> tuple[ArtifactWorkflow, MagicMock]:
        workflow = ArtifactWorkflow(make_config())
        mock_api = MagicMock()
        workflow._api = mock_api
        return workflow, mock_api

    # ── 正常流程：触发→轮询→出现新 artifact→下载 ─────────────────

    def test_trigger_and_download_success(self, tmp_path):
        """正常流程：触发后轮询到新 artifact，下载到指定路径。"""
        workflow, mock_api = self._make_workflow()
        save_path = str(tmp_path / "export.zip")

        mock_api.get_artifacts.side_effect = [
            {"createdAt": "2025-01-01T00:00:00.000Z"},  # 触发前的旧记录
            {"createdAt": "2025-01-01T01:00:00.000Z"},  # 触发后的新记录
        ]
        mock_api.download_artifacts.side_effect = self._write_artifact

        with patch("transbridge.paratranz.workflow.artifact.time.sleep"):
            with patch(
                "transbridge.paratranz.workflow.artifact.time.monotonic", side_effect=[0, 0]
            ):  # deadline=305, while 0<305 → True → break
                result = workflow.trigger_and_download(1, save_path)

        assert result == save_path
        mock_api.trigger_export.assert_called_once_with(1)
        mock_api.download_artifacts.assert_called_once()
        assert mock_api.download_artifacts.call_args.args[0] == 1
        assert mock_api.download_artifacts.call_args.args[1].endswith(".part")
        assert Path(save_path).exists()

    # ── 首次导出（无历史 artifact）也能正常工作 ───────────────────

    def test_trigger_and_download_no_initial_artifact(self, tmp_path):
        """项目从未导出过，t0=None，新 artifact 出现后应正常下载。"""
        workflow, mock_api = self._make_workflow()
        save_path = str(tmp_path / "export.zip")

        mock_api.get_artifacts.side_effect = [
            None,  # 首次：无历史记录
            {"createdAt": "2025-01-01T01:00:00.000Z"},  # 轮询：新记录
        ]
        mock_api.download_artifacts.side_effect = self._write_artifact

        with patch("transbridge.paratranz.workflow.artifact.time.sleep"):
            with patch("transbridge.paratranz.workflow.artifact.time.monotonic", side_effect=[0, 0]):
                result = workflow.trigger_and_download(1, save_path)

        mock_api.download_artifacts.assert_called_once()
        assert result == save_path

    # ── 进度回调 ────────────────────────────────────────────────

    def test_trigger_and_download_progress_callback(self, tmp_path):
        """进度回调应收到若干状态消息。"""
        workflow, mock_api = self._make_workflow()
        mock_api.get_artifacts.side_effect = [
            {"createdAt": "2025-01-01T00:00:00.000Z"},
            {"createdAt": "2025-01-01T01:00:00.000Z"},
        ]
        mock_api.download_artifacts.side_effect = self._write_artifact
        messages = []

        with patch("transbridge.paratranz.workflow.artifact.time.sleep"):
            with patch("transbridge.paratranz.workflow.artifact.time.monotonic", side_effect=[0, 0]):
                workflow.trigger_and_download(
                    1,
                    str(tmp_path / "export.zip"),
                    progress_callback=messages.append,
                )

        assert len(messages) >= 3  # 触发中、等待中、下载中、完成 至少 3 条

    # ── 超时场景 ────────────────────────────────────────────────

    def test_trigger_and_download_timeout(self, tmp_path):
        """超时后应抛出 TimeoutError，不调用 download_artifacts。"""
        workflow, mock_api = self._make_workflow()

        # get_artifacts 始终返回旧 createdAt，不出现新记录
        mock_api.get_artifacts.return_value = {"createdAt": "2025-01-01T00:00:00.000Z"}

        with patch("transbridge.paratranz.workflow.artifact.time.sleep"):
            # monotonic: 第1次(deadline计算)=0, 第2次(while判断)=999 → 超出 timeout=5
            with patch("transbridge.paratranz.workflow.artifact.time.monotonic", side_effect=[0, 999]):
                with pytest.raises(TimeoutError, match="导出超时"):
                    workflow.trigger_and_download(
                        1,
                        str(tmp_path / "export.zip"),
                        poll_interval=0.001,
                        timeout=0.002,
                    )

        mock_api.download_artifacts.assert_not_called()

    # ── get_artifacts 抛异常时跳过继续轮询 ──────────────────────

    def test_trigger_and_download_polls_through_api_error(self, tmp_path):
        """轮询期间 get_artifacts 偶发异常时应继续等待，不中断。"""
        workflow, mock_api = self._make_workflow()
        save_path = str(tmp_path / "export.zip")

        mock_api.get_artifacts.side_effect = [
            {"createdAt": "2025-01-01T00:00:00.000Z"},  # 初始
            RuntimeError("timeout"),  # 第1次轮询失败
            {"createdAt": "2025-01-01T01:00:00.000Z"},  # 第2次轮询成功
        ]
        mock_api.download_artifacts.side_effect = self._write_artifact

        with patch("transbridge.paratranz.workflow.artifact.time.sleep"):
            with patch("transbridge.paratranz.workflow.artifact.time.monotonic", side_effect=[0, 0, 0]):
                result = workflow.trigger_and_download(1, save_path, poll_interval=0.001)

        mock_api.download_artifacts.assert_called_once()
        assert result == save_path

    # ── extract：正确解压 zip ────────────────────────────────────

    def test_extract(self, tmp_path):
        """extract() 应将 zip 内文件解压到目标目录并返回路径列表。"""
        workflow, _ = self._make_workflow()

        # 创建测试用 zip
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("人名.json", json.dumps([{"key": "a", "original": "A"}]))
            zf.writestr("物品.json", json.dumps([]))

        extract_dir = tmp_path / "extracted"
        extracted = workflow.extract(str(zip_path), str(extract_dir))

        assert len(extracted) == 2
        assert extract_dir.is_dir()
        names = {Path(p).name for p in extracted}
        assert names == {"人名.json", "物品.json"}

    # ── extract：目标目录不存在时自动创建 ───────────────────────

    def test_extract_creates_dir(self, tmp_path):
        """extract_dir 不存在时应自动创建。"""
        workflow, _ = self._make_workflow()

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("a.json", "[]")

        extract_dir = tmp_path / "new" / "deep" / "dir"
        assert not extract_dir.exists()

        workflow.extract(str(zip_path), str(extract_dir))
        assert extract_dir.is_dir()
