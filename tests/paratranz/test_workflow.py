"""
WorkFlow 单元测试：ParaTranzUploader / ParaTranzDownloader / ArtifactWorkflow

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
from transbridge.paratranz.workflow.downloader import ParaTranzDownloader
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


def pt_string(key: str, translation: str = "", stage: int = 1, original: str = "original") -> dict:
    """构造 ParaTranz 词条格式的 dict（模拟 get_file_translation 返回值）"""
    return {
        "id": 999,
        "key": key,
        "original": original,
        "translation": translation,
        "stage": stage,
        "context": "NPC_:FULL",
    }


# ─────────────────────────────────────────────────────────────
# ParaTranzUploader
# ─────────────────────────────────────────────────────────────


class TestParaTranzUploader:
    def _make_uploader(self) -> tuple[ParaTranzUploader, MagicMock]:
        uploader = ParaTranzUploader(make_config())
        mock_api = MagicMock()
        uploader._api = mock_api
        return uploader, mock_api

    def _patch_export(self, file_names: list[str]):
        """返回一个 patch 上下文，export 调用时在 output_dir 创建指定文件。"""

        def side_effect(collection, output_dir, **kwargs):
            for name in file_names:
                (Path(output_dir) / name).write_text("[]", encoding="utf-8")

        return patch(
            "transbridge.paratranz.workflow.uploader.export_to_categorized_json_files",
            side_effect=side_effect,
        )

    # ── 正常上传新文件 ──────────────────────────────────────────

    def test_upload_new_files(self):
        """项目中不存在同名文件时，应调用 upload_file 新建。"""
        uploader, mock_api = self._make_uploader()
        mock_api.list_files.return_value = []  # 项目空空如也

        with self._patch_export(["人名.json", "物品.json"]):
            result = uploader.upload_collection(make_collection(), project_id=1)

        assert result.created == 2
        assert result.updated == 0
        assert result.skipped == 0
        assert set(result.files) == {"人名.json", "物品.json"}
        assert mock_api.upload_file.call_count == 2
        assert mock_api.reupload_file.call_count == 0

    # ── 更新已有文件 ────────────────────────────────────────────

    def test_upload_updates_existing_files(self):
        """项目中已存在同名文件时，应调用 reupload_file 更新原文。"""
        uploader, mock_api = self._make_uploader()
        mock_api.list_files.return_value = [
            {"id": 101, "name": "人名.json"},
        ]

        with self._patch_export(["人名.json", "物品.json"]):
            result = uploader.upload_collection(make_collection(), project_id=1)

        assert result.created == 1  # 物品.json
        assert result.updated == 1  # 人名.json
        assert result.skipped == 0
        # reupload_file 收到正确的 file_id
        mock_api.reupload_file.assert_called_once()
        args = mock_api.reupload_file.call_args
        assert args.args[1] == 101  # file_id

    # ── 混合情况 ────────────────────────────────────────────────

    def test_upload_mixed_new_and_existing(self):
        """一部分文件已存在，一部分需新建。"""
        uploader, mock_api = self._make_uploader()
        mock_api.list_files.return_value = [
            {"id": 10, "name": "人名.json"},
            {"id": 11, "name": "书籍_书名.json"},
        ]

        with self._patch_export(["人名.json", "书籍_书名.json", "物品.json"]):
            result = uploader.upload_collection(make_collection(), project_id=1)

        assert result.created == 1
        assert result.updated == 2
        assert result.skipped == 0

    # ── 单文件上传失败不影响其他文件 ────────────────────────────

    def test_upload_skips_on_api_error(self):
        """单个文件上传失败时跳过，不中断整体流程。"""
        uploader, mock_api = self._make_uploader()
        mock_api.list_files.return_value = []
        mock_api.upload_file.side_effect = [
            RuntimeError("server error"),  # 第一个文件失败
            {"id": 2, "name": "物品.json"},  # 第二个成功
        ]

        with self._patch_export(["人名.json", "物品.json"]):
            result = uploader.upload_collection(make_collection(), project_id=1)

        assert result.created == 1
        assert result.skipped == 1

    # ── 集合为空，导出零文件 ────────────────────────────────────

    def test_upload_empty_collection_returns_empty_result(self):
        """导出零文件时，直接返回空结果，不调用任何 API。"""
        uploader, mock_api = self._make_uploader()

        with self._patch_export([]):  # 没有文件被导出
            result = uploader.upload_collection(make_collection(), project_id=1)

        assert result.created == 0
        assert result.updated == 0
        mock_api.upload_file.assert_not_called()
        mock_api.reupload_file.assert_not_called()

    # ── 进度回调 ────────────────────────────────────────────────

    def test_upload_progress_callback(self):
        """进度回调应在每个文件处理时被调用，最后以"完成"结束。"""
        uploader, mock_api = self._make_uploader()
        mock_api.list_files.return_value = []
        calls = []

        with self._patch_export(["人名.json"]):
            uploader.upload_collection(
                make_collection(),
                project_id=1,
                progress_callback=lambda cur, tot, name: calls.append((cur, tot, name)),
            )

        assert any(name == "完成" for _, _, name in calls)

    # ── list_files 失败应抛出 RuntimeError ──────────────────────

    def test_upload_raises_when_list_files_fails(self):
        uploader, mock_api = self._make_uploader()
        mock_api.list_files.side_effect = RuntimeError("network error")

        with self._patch_export(["人名.json"]):
            with pytest.raises(RuntimeError, match="获取项目文件列表失败"):
                uploader.upload_collection(make_collection(), project_id=1)


# ─────────────────────────────────────────────────────────────
# ParaTranzDownloader
# ─────────────────────────────────────────────────────────────


class TestParaTranzDownloader:
    def _make_downloader(self) -> tuple[ParaTranzDownloader, MagicMock]:
        downloader = ParaTranzDownloader(make_config())
        mock_api = MagicMock()
        downloader._api = mock_api
        return downloader, mock_api

    # ── 正常合并已翻译词条 ──────────────────────────────────────

    def test_download_merges_translated_strings(self):
        """stage >= 1 且有译文的词条应合并到本地集合。"""
        downloader, mock_api = self._make_downloader()
        mock_api.list_files.return_value = [{"id": 10, "name": "人名.json"}]
        mock_api.get_file_translation.return_value = [
            pt_string("key:001|1~NPC_:FULL", translation="奥里", stage=1),
            pt_string("key:002|1~NPC_:FULL", translation="盗贼", stage=3),
        ]

        collection = make_collection(
            make_entry("key:001|1~NPC_:FULL", original="Auri"),
            make_entry("key:002|1~NPC_:FULL", original="Bandit"),
        )
        identities = {entry.key: entry.identity for entry in collection}

        result = downloader.download_to_collection(1, collection)

        assert result.merged == 2
        assert result.skipped_low_stage == 0
        assert result.skipped_no_match == 0
        assert collection.get("key:001|1~NPC_:FULL").translation == "奥里"
        assert collection.get("key:002|1~NPC_:FULL").translation == "盗贼"
        assert collection.get("key:002|1~NPC_:FULL").stage == 3
        assert collection.get("key:001|1~NPC_:FULL").identity == identities["key:001|1~NPC_:FULL"]
        assert collection.get("key:001|1~NPC_:FULL").revision.value == 1

    # ── 跳过未翻译词条 ──────────────────────────────────────────

    def test_download_skips_untranslated(self):
        """stage=0 的词条不应合并。"""
        downloader, mock_api = self._make_downloader()
        mock_api.list_files.return_value = [{"id": 10, "name": "人名.json"}]
        mock_api.get_file_translation.return_value = [
            pt_string("key:001|1~NPC_:FULL", translation="", stage=0),
        ]

        collection = make_collection(make_entry("key:001|1~NPC_:FULL"))
        result = downloader.download_to_collection(1, collection)

        assert result.merged == 0
        assert result.skipped_low_stage == 1
        assert collection.get("key:001|1~NPC_:FULL").translation == ""

    # ── 跳过译文为空的词条 ──────────────────────────────────────

    def test_download_skips_empty_translation(self):
        """stage >= 1 但译文为空字符串时也应跳过。"""
        downloader, mock_api = self._make_downloader()
        mock_api.list_files.return_value = [{"id": 10, "name": "人名.json"}]
        mock_api.get_file_translation.return_value = [
            pt_string("key:001|1~NPC_:FULL", translation="", stage=1),
        ]

        collection = make_collection(make_entry("key:001|1~NPC_:FULL"))
        result = downloader.download_to_collection(1, collection)

        assert result.merged == 0
        assert result.skipped_low_stage == 1

    # ── 跳过本地不存在的 key ────────────────────────────────────

    def test_download_skips_no_match(self):
        """ParaTranz 返回的 key 在本地集合不存在时跳过。"""
        downloader, mock_api = self._make_downloader()
        mock_api.list_files.return_value = [{"id": 10, "name": "人名.json"}]
        mock_api.get_file_translation.return_value = [
            pt_string("key:999|1~NPC_:FULL", translation="某人", stage=1),
        ]

        collection = make_collection(make_entry("key:001|1~NPC_:FULL"))
        result = downloader.download_to_collection(1, collection)

        assert result.merged == 0
        assert result.skipped_no_match == 1

    # ── 单文件下载失败不影响其他文件 ────────────────────────────

    def test_download_continues_on_file_error(self):
        """一个文件 get_file_translation 失败时跳过，继续处理其余文件。"""
        downloader, mock_api = self._make_downloader()
        mock_api.list_files.return_value = [
            {"id": 10, "name": "人名.json"},
            {"id": 11, "name": "物品.json"},
        ]
        mock_api.get_file_translation.side_effect = [
            RuntimeError("timeout"),
            [pt_string("key:001|1~NPC_:FULL", translation="奥里", stage=1)],
        ]

        collection = make_collection(make_entry("key:001|1~NPC_:FULL"))
        result = downloader.download_to_collection(1, collection)

        assert result.merged == 1
        assert collection.get("key:001|1~NPC_:FULL").translation == "奥里"

    # ── min_stage 过滤 ──────────────────────────────────────────

    def test_download_min_stage_filter(self):
        """min_stage=3 时，只合并 stage >= 3 的词条。"""
        downloader, mock_api = self._make_downloader()
        mock_api.list_files.return_value = [{"id": 10, "name": "人名.json"}]
        mock_api.get_file_translation.return_value = [
            pt_string("key:001|1~NPC_:FULL", translation="已翻译", stage=1),
            pt_string("key:002|1~NPC_:FULL", translation="已检查", stage=3),
        ]

        collection = make_collection(
            make_entry("key:001|1~NPC_:FULL"),
            make_entry("key:002|1~NPC_:FULL"),
        )
        result = downloader.download_to_collection(1, collection, min_stage=3)

        assert result.merged == 1
        assert result.skipped_low_stage == 1
        assert collection.get("key:001|1~NPC_:FULL").translation == ""  # 未合并
        assert collection.get("key:002|1~NPC_:FULL").translation == "已检查"

    # ── 多文件累计统计 ──────────────────────────────────────────

    def test_download_accumulates_across_files(self):
        """多个文件的合并结果应累加到同一个 DownloadResult。"""
        downloader, mock_api = self._make_downloader()
        mock_api.list_files.return_value = [
            {"id": 10, "name": "人名.json"},
            {"id": 11, "name": "物品.json"},
        ]
        mock_api.get_file_translation.side_effect = [
            [pt_string("key:001|1~NPC_:FULL", translation="奥里", stage=1)],
            [pt_string("key:002|1~WEAP:FULL", translation="奥里之弓", stage=1)],
        ]

        collection = make_collection(
            make_entry("key:001|1~NPC_:FULL"),
            make_entry("key:002|1~WEAP:FULL"),
        )
        result = downloader.download_to_collection(1, collection)

        assert result.merged == 2
        assert result.total_strings == 2

    # ── 进度回调 ────────────────────────────────────────────────

    def test_download_progress_callback(self):
        """进度回调应为每个文件调用一次，最后一次为"完成"。"""
        downloader, mock_api = self._make_downloader()
        mock_api.list_files.return_value = [{"id": 10, "name": "人名.json"}]
        mock_api.get_file_translation.return_value = []
        calls = []

        downloader.download_to_collection(
            1,
            make_collection(),
            progress_callback=lambda cur, tot, name: calls.append((cur, tot, name)),
        )

        assert any(name == "完成" for _, _, name in calls)

    # ── list_files 失败应抛出 RuntimeError ──────────────────────

    def test_download_raises_when_list_files_fails(self):
        downloader, mock_api = self._make_downloader()
        mock_api.list_files.side_effect = RuntimeError("network error")

        with pytest.raises(RuntimeError, match="获取项目文件列表失败"):
            downloader.download_to_collection(1, make_collection())


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
