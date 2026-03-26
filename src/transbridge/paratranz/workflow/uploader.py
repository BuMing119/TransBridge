"""
ParaTranzUploader：将本地 TranslationEntryCollection 上传到 ParaTranz。

工作流：
  1. 将集合导出为分类 JSON 文件（临时目录）
  2. 获取项目中已有文件列表
  3. 对每个分类文件按 translation_mode 处理
  4. 返回 UploadResult（新建数、更新数、跳过数、译文导入数）

translation_mode 取值：
  "orig_only"   — 仅更新原文（默认）；新建文件正常创建，已有文件只更新原文，不碰译文
  "trans_safe"  — 仅导入译文，不覆盖已人工编辑的词条；新建文件跳过（无 file_id）
  "trans_force" — 仅导入译文，强制覆盖所有译文；新建文件跳过
  "both"        — 更新原文并安全导入译文；新建文件创建后再导入译文
"""

import json
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
    created: int = 0              # 新建文件数
    updated: int = 0              # 更新原文文件数
    skipped: int = 0              # 因错误跳过的文件数
    translation_updated: int = 0  # 成功导入译文的文件数
    files: list[str] = field(default_factory=list)  # 成功处理的文件名列表


class ParaTranzUploader:

    def __init__(self, config: ParatranzConfig):
        self._api = ParatranzFilesAPI(token=config.token, config=config)

    def upload_collection(
        self,
        collection: TranslationEntryCollection,
        project_id: int,
        *,
        file_filter: set[str] | None = None,
        translation_mode: str = "orig_only",
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> UploadResult:
        """
        将 TranslationEntryCollection 按分类上传到 ParaTranz 项目。

        Args:
            collection:         本地翻译集合
            project_id:         ParaTranz 项目 ID
            file_filter:        若指定，则只上传文件名在此集合内的文件；None 表示全部上传
            translation_mode:   处理方式：
                                  "orig_only"   — 仅更新原文（默认）
                                  "trans_safe"  — 仅导入译文，不覆盖人工编辑；新建文件跳过
                                  "trans_force" — 仅导入译文，强制覆盖；新建文件跳过
                                  "both"        — 更新原文并安全导入译文
            progress_callback:  进度回调 (current, total, filename)

        Returns:
            UploadResult
        """
        result = UploadResult()

        do_reupload = translation_mode in ("orig_only", "both")
        do_trans = translation_mode in ("trans_safe", "trans_force", "both")
        force_trans = translation_mode == "trans_force"

        with tempfile.TemporaryDirectory() as tmp_dir:
            # 1. 导出分类 JSON 到临时目录
            export_to_categorized_json_files(collection, tmp_dir)
            json_files = sorted(Path(tmp_dir).glob("*.json"))

            if file_filter is not None:
                json_files = [f for f in json_files if f.name in file_filter]

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

            # 3. 逐文件处理
            for i, json_path in enumerate(json_files):
                name = json_path.name
                if progress_callback:
                    progress_callback(i, total, name)

                try:
                    if name in existing:
                        file_id = existing[name]
                        if do_reupload:
                            self._api.reupload_file(project_id, file_id, str(json_path))
                            result.updated += 1
                        if do_trans:
                            try:
                                self._api.update_file_translation(project_id, file_id, str(json_path), force=force_trans)
                                result.translation_updated += 1
                            except RuntimeError:
                                if not do_reupload:
                                    raise  # 纯译文模式下译文失败视为跳过
                        result.files.append(name)
                    else:
                        if not do_reupload:
                            pass  # 纯译文模式：新建文件跳过
                        else:
                            resp = self._api.upload_file(project_id, str(json_path))
                            result.created += 1
                            if do_trans and isinstance(resp, dict):
                                new_file_id = resp.get("id")
                                if new_file_id:
                                    try:
                                        self._api.update_file_translation(project_id, new_file_id, str(json_path), force=force_trans)
                                        result.translation_updated += 1
                                    except RuntimeError:
                                        pass
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
        translation_mode: str = "orig_only",
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> UploadResult:
        """
        将 TranslationEntryCollection 合并为单个 JSON 文件上传到 ParaTranz 项目。
        若文件过大被服务端拒绝，自动对半拆分并以序号命名（如 file_1.json、file_2.json），
        拆分后若仍过大则继续递归拆分，序号保持连续。

        Args:
            collection:         本地翻译集合
            project_id:         ParaTranz 项目 ID
            filename:           上传后在 ParaTranz 中显示的文件名（如 "AuriBoss.json"）
            translation_mode:   已存在文件的译文处理方式（同 upload_collection）
            progress_callback:  进度回调 (current, total, filename)

        Returns:
            UploadResult
        """
        result = UploadResult()

        with tempfile.TemporaryDirectory() as tmp_dir:
            entries = collection.to_dict()

            try:
                file_list = self._api.list_files(project_id) or []
                existing = {f["name"]: f["id"] for f in file_list}
            except RuntimeError as e:
                raise RuntimeError(f"获取项目文件列表失败：{e}") from e

            stem = Path(filename).stem
            ext = Path(filename).suffix or ".json"
            counter = [1]  # 可变计数器，仅在成功上传后递增

            self._upload_entries_recursive(
                entries, stem, ext, project_id, existing, result, tmp_dir, counter,
                translation_mode=translation_mode,
            )

            if progress_callback:
                progress_callback(1, 1, "完成")

        return result

    def _upload_entries_recursive(
        self,
        entries: list,
        stem: str,
        ext: str,
        project_id: int,
        existing: dict[str, int],
        result: "UploadResult",
        tmp_dir: str,
        counter: list[int],
        *,
        translation_mode: str = "orig_only",
        is_split: bool = False,
    ) -> None:
        """
        递归上传词条列表。
        - 首次上传：使用原始文件名（无后缀）
        - 分割后上传：使用序号后缀命名（如 file_1.json、file_2.json）
        - 单条词条仍过大：记为跳过并抛出警告
        """
        do_reupload = translation_mode in ("orig_only", "both")
        do_trans = translation_mode in ("trans_safe", "trans_force", "both")
        force_trans = translation_mode == "trans_force"

        # 首次上传用原始文件名，分割后才添加序号后缀
        if is_split:
            name = f"{stem}_{counter[0]}{ext}"
        else:
            name = f"{stem}{ext}"
        json_path = Path(tmp_dir) / name
        json_path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        try:
            if name in existing:
                file_id = existing[name]
                if do_reupload:
                    self._api.reupload_file(project_id, file_id, str(json_path))
                    result.updated += 1
                if do_trans:
                    try:
                        self._api.update_file_translation(project_id, file_id, str(json_path), force=force_trans)
                        result.translation_updated += 1
                    except RuntimeError:
                        if not do_reupload:
                            raise
                result.files.append(name)
                # 分割模式下才递增计数器
                if is_split:
                    counter[0] += 1
            else:
                if not do_reupload:
                    pass  # 纯译文模式：新建文件跳过
                else:
                    resp = self._api.upload_file(project_id, str(json_path))
                    result.created += 1
                    if do_trans and isinstance(resp, dict):
                        new_file_id = resp.get("id")
                        if new_file_id:
                            try:
                                self._api.update_file_translation(project_id, new_file_id, str(json_path), force=force_trans)
                                result.translation_updated += 1
                            except RuntimeError:
                                pass
                    result.files.append(name)
                    # 分割模式下才递增计数器
                    if is_split:
                        counter[0] += 1
        except RuntimeError as e:
            err = str(e)
            if ("too large" in err.lower() or "413" in err) and len(entries) > 1:
                mid = len(entries) // 2
                # 分割后递归上传，启用序号后缀
                self._upload_entries_recursive(
                    entries[:mid], stem, ext, project_id, existing, result, tmp_dir, counter,
                    translation_mode=translation_mode,
                    is_split=True,
                )
                self._upload_entries_recursive(
                    entries[mid:], stem, ext, project_id, existing, result, tmp_dir, counter,
                    translation_mode=translation_mode,
                    is_split=True,
                )
            else:
                result.skipped += 1
                raise RuntimeError(
                    f"上传 {name} 失败（共 {len(entries)} 条词条）：{err}"
                ) from e
