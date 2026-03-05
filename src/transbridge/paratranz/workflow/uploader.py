"""
ParaTranzUploader：将本地 TranslationEntryCollection 上传到 ParaTranz。

工作流：
  1. 将集合导出为分类 JSON 文件（临时目录）
  2. 获取项目中已有文件列表
  3. 对每个分类文件：同名已存在 → reupload_file（更新原文），不存在 → upload_file（新建）
  4. 返回 UploadResult（新建数、更新数、跳过数）
"""

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.converter.translation_entry_collection_export import export_to_categorized_json_files
from src.transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
from src.transbridge.paratranz.config_manager import ParatranzConfig


@dataclass
class UploadResult:
    """上传操作的结果摘要"""
    created: int = 0        # 新建文件数
    updated: int = 0        # 更新原文文件数
    skipped: int = 0        # 因错误跳过的文件数
    files: list[str] = field(default_factory=list)  # 成功处理的文件名列表


class ParaTranzUploader:

    def __init__(self, config: ParatranzConfig):
        self._api = ParatranzFilesAPI(token=config.token, config=config)

    def upload_collection(
        self,
        collection: TranslationEntryCollection,
        project_id: int,
        *,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> UploadResult:
        """
        将 TranslationEntryCollection 按分类上传到 ParaTranz 项目。

        Args:
            collection:         本地翻译集合
            project_id:         ParaTranz 项目 ID
            progress_callback:  进度回调 (current, total, filename)

        Returns:
            UploadResult
        """
        result = UploadResult()

        with tempfile.TemporaryDirectory() as tmp_dir:
            # 1. 导出分类 JSON 到临时目录
            export_to_categorized_json_files(collection, tmp_dir)
            json_files = sorted(Path(tmp_dir).glob("*.json"))

            if not json_files:
                return result

            # 2. 获取项目中已有文件，建立 name -> id 映射
            existing: dict[str, int] = {}
            try:
                file_list = self._api.list_files(project_id) or []
                existing = {f["name"]: f["id"] for f in file_list}
            except RuntimeError as e:
                raise RuntimeError(f"获取项目文件列表失败：{e}") from e

            total = len(json_files)

            # 3. 逐文件上传或更新
            for i, json_path in enumerate(json_files):
                name = json_path.name
                if progress_callback:
                    progress_callback(i, total, name)

                try:
                    if name in existing:
                        self._api.reupload_file(project_id, existing[name], str(json_path))
                        result.updated += 1
                    else:
                        self._api.upload_file(project_id, str(json_path))
                        result.created += 1
                    result.files.append(name)
                except RuntimeError:
                    result.skipped += 1

            if progress_callback:
                progress_callback(total, total, "完成")

        return result

    def upload_collection_as_single(
        self,
        collection: TranslationEntryCollection,
        project_id: int,
        filename: str,
        *,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> UploadResult:
        """
        将 TranslationEntryCollection 合并为单个 JSON 文件上传到 ParaTranz 项目。

        Args:
            collection:         本地翻译集合
            project_id:         ParaTranz 项目 ID
            filename:           上传后在 ParaTranz 中显示的文件名（如 "AuriBoss.json"）
            progress_callback:  进度回调 (current, total, filename)

        Returns:
            UploadResult
        """
        result = UploadResult()

        with tempfile.TemporaryDirectory() as tmp_dir:
            json_path = Path(tmp_dir) / filename
            json_path.write_text(collection.to_json(), encoding="utf-8")

            try:
                file_list = self._api.list_files(project_id) or []
                existing = {f["name"]: f["id"] for f in file_list}
            except RuntimeError as e:
                raise RuntimeError(f"获取项目文件列表失败：{e}") from e

            if progress_callback:
                progress_callback(0, 1, filename)

            try:
                if filename in existing:
                    self._api.reupload_file(project_id, existing[filename], str(json_path))
                    result.updated += 1
                else:
                    self._api.upload_file(project_id, str(json_path))
                    result.created += 1
                result.files.append(filename)
            except RuntimeError:
                result.skipped += 1

            if progress_callback:
                progress_callback(1, 1, "完成")

        return result
