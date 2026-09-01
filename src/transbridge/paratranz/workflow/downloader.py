"""
ParaTranzDownloader：从 ParaTranz 下载译文并合并到本地 TranslationEntryCollection。

工作流：
  1. 获取项目所有文件列表
  2. 对每个文件调用 get_file_translation，拿到带译文的词条列表
  3. 按 key 字段匹配本地集合（ParaTranz key == 本地 entry.id）
  4. 仅合并 stage >= min_stage 且 translation 非空的词条
  5. 返回 DownloadResult（合并数、未匹配数、跳过数、总词条数）
"""

from collections.abc import Callable
from dataclasses import dataclass, replace

from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
from transbridge.paratranz.config_manager import ParatranzConfig


@dataclass
class DownloadResult:
    """下载合并操作的结果摘要"""

    merged: int = 0  # 成功合并到本地集合的词条数
    skipped_no_match: int = 0  # key 在本地集合中不存在的词条数
    skipped_low_stage: int = 0  # stage 不足或译文为空，跳过的词条数
    total_strings: int = 0  # 从 ParaTranz 拉取的词条总数


class ParaTranzDownloader:
    def __init__(self, config: ParatranzConfig):
        self._api = ParatranzFilesAPI(token=config.token, config=config)

    def download_to_collection(
        self,
        project_id: int,
        collection: TranslationEntryCollection,
        *,
        min_stage: int = 1,
        file_ids: list[int] | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> DownloadResult:
        """
        从 ParaTranz 项目下载译文，按 key 合并到本地集合。

        Args:
            project_id:         ParaTranz 项目 ID
            collection:         本地翻译集合（原地修改）
            min_stage:          最低接受的词条状态，默认 1（已翻译及以上）
            file_ids:           仅处理指定 ID 的文件；None 表示处理全部文件
            progress_callback:  进度回调 (current, total, filename)

        Returns:
            DownloadResult
        """
        result = DownloadResult()

        try:
            files = self._api.list_files(project_id) or []
        except RuntimeError as e:
            raise RuntimeError(f"获取项目文件列表失败：{e}") from e

        if file_ids is not None:
            allowed = set(file_ids)
            files = [f for f in files if f["id"] in allowed]

        total = len(files)

        for i, file_info in enumerate(files):
            file_id = file_info["id"]
            file_name = file_info.get("name", str(file_id))

            if progress_callback:
                progress_callback(i, total, file_name)

            try:
                strings = self._api.get_file_translation(project_id, file_id)
            except RuntimeError:
                # 单个文件获取失败不影响其他文件
                continue

            if not isinstance(strings, list):
                continue

            for item in strings:
                result.total_strings += 1

                key = item.get("key", "")
                stage = item.get("stage", 0)  # ParaTranz API stage 值直接透传（0/1/2/3/5/9/-1）
                translation = item.get("translation", "")

                # 跳过未达到最低状态或译文为空的词条
                if stage < min_stage or not translation:
                    result.skipped_low_stage += 1
                    continue

                # 按 key 匹配本地词条（ParaTranz key == 本地 entry.id）
                entry = collection.get(key)
                if entry is None:
                    result.skipped_no_match += 1
                    continue

                # 保留 V2 EntryKey、远端引用和来源证据，只更新本地可变状态。
                changed = (entry.translation, entry.stage) != (translation, stage)
                collection.add(
                    replace(
                        entry,
                        translation=translation,
                        stage=stage,  # 直接透传 ParaTranz stage，对齐 STAGE_* 常量语义
                        revision=entry.revision.next() if changed else entry.revision,
                    ),
                    overwrite=True,
                )

                result.merged += 1

        if progress_callback:
            progress_callback(total, total, "完成")

        return result
