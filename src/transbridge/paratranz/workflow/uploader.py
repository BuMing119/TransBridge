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
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.converter.translation_entry_collection_export import export_to_categorized_json_files
from src.transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
from src.transbridge.paratranz.config_manager import ParatranzConfig


@dataclass
class ConflictInfo:
    """单个文件名冲突的信息"""
    local_name: str           # 本地文件名（如 "人名.json"）
    candidates: list[dict]    # ParaTranz 上所有同名文件，每个包含 id, name, folder 字段


@dataclass
class FileMaps:
    """文件列表的三种映射（避免重复 API 调用）"""
    existing: dict[str, int]              # name → file_id
    path_based: dict[str, int]            # full_path → file_id
    name_to_files: dict[str, list[dict]]  # name → [file_info_list]


@dataclass
class UploadResult:
    """上传操作的结果摘要"""
    created: int = 0              # 新建文件数
    updated: int = 0              # 更新原文文件数
    skipped: int = 0              # 因错误跳过的文件数
    translation_updated: int = 0  # 成功导入译文的文件数
    files: list[str] = field(default_factory=list)  # 成功处理的文件名列表
    name_conflicts: dict[str, list[dict]] = field(default_factory=dict)  # 同名文件冲突信息


class ParaTranzUploader:

    def __init__(self, config: ParatranzConfig):
        self._api = ParatranzFilesAPI(token=config.token, config=config)

    def _fetch_file_maps(self, project_id: int) -> tuple[dict[str, int], dict[str, int], dict[str, list[dict]]]:
        """
        获取项目文件列表，返回三个映射：
          - existing:          文件名 → file_id（同名取最后一个）
          - path_based:        完整路径 → file_id
          - name_to_files:     文件名 → [文件信息列表]（用于冲突检测）
        """
        import logging
        logger = logging.getLogger(__name__)

        file_list = self._api.list_files(project_id) or []
        logger.info(f"[ParaTranzUploader] 从项目 {project_id} 获取到 {len(file_list)} 个文件")

        existing: dict[str, int] = {}
        path_based: dict[str, int] = {}
        name_to_files: dict[str, list[dict]] = defaultdict(list)

        for f in file_list:
            full_name = f["name"]
            folder = f.get("folder", "")

            if "/" in full_name:
                parts = full_name.rsplit("/", 1)
                actual_folder = parts[0]
                actual_name = parts[1]
                f = dict(f, folder=actual_folder, name=actual_name)  # 不修改原始 dict
            else:
                actual_name = full_name
                actual_folder = folder

            name_to_files[actual_name].append(f)
            existing[actual_name] = f["id"]
            full_path = f"{actual_folder}/{actual_name}" if actual_folder else actual_name
            path_based[full_path] = f["id"]

        return existing, path_based, name_to_files

    def detect_conflicts(
        self,
        project_id: int,
        file_names: set[str],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[list[ConflictInfo], FileMaps]:
        """
        上传前检测冲突：查找本地文件名在 ParaTranz 中有多个同名文件的情况。

        Args:
            project_id:  ParaTranz 项目 ID
            file_names:  本次要上传的本地文件名集合
            progress_callback:  进度回调 (current, total, filename)

        Returns:
            (conflicts, file_maps) 元组：
            - conflicts: list[ConflictInfo]，只包含有冲突（多个候选）的文件
            - file_maps: FileMaps，已获取的文件映射，供后续 upload_collection 使用以避免重复 API 调用
        """
        if progress_callback:
            progress_callback(0, 1, "正在获取 ParaTranz 文件列表...")

        existing, path_based, name_to_files = self._fetch_file_maps(project_id)
        conflicts = []

        if progress_callback:
            progress_callback(1, 1, "正在分析文件冲突...")

        for name in file_names:
            candidates = name_to_files.get(name, [])
            if len(candidates) > 1:
                conflicts.append(ConflictInfo(local_name=name, candidates=candidates))

        if progress_callback:
            progress_callback(1, 1, f"检测到 {len(conflicts)} 个冲突")

        return conflicts, FileMaps(existing, path_based, name_to_files)

    def upload_collection(
        self,
        collection: TranslationEntryCollection,
        project_id: int,
        *,
        file_filter: set[str] | None = None,
        translation_mode: str = "orig_only",
        progress_callback: Callable[[int, int, str], None] | None = None,
        path_mapping: dict[str, str] | None = None,
        file_id_override: dict[str, int] | None = None,
        prefetched_maps: FileMaps | None = None,
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
            path_mapping:       文件名到完整路径的映射，用于ParaTranz中文件被移动后的场景。
                                例如：{"人名.json": "上古卷轴5/人物/人名.json"}
            file_id_override:   文件名到 file_id 的直接映射，优先级最高（用于用户手动解决同名冲突）。
                                例如：{"人名.json": 12345}
            prefetched_maps:    预获取的文件映射（来自 detect_conflicts），避免重复 API 调用。

        Returns:
            UploadResult（包含 name_conflicts 字段，用于检测同名文件冲突）
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

            # 2. 获取项目中已有文件，建立映射
            if prefetched_maps is not None:
                existing = prefetched_maps.existing
                path_based_existing = prefetched_maps.path_based
                name_to_files = prefetched_maps.name_to_files
            else:
                try:
                    existing, path_based_existing, name_to_files = self._fetch_file_maps(project_id)
                except RuntimeError as e:
                    raise RuntimeError(f"获取项目文件列表失败：{e}") from e

            # 检测同名文件冲突（同名但不同路径）
            conflicts: dict[str, list[dict]] = {
                name: files for name, files in name_to_files.items()
                if len(files) > 1 or (len(files) == 1 and bool(files[0].get("folder", "")))
            }
            if conflicts:
                result.name_conflicts = conflicts

            total = len(json_files)

            # 3. 逐文件处理
            for i, json_path in enumerate(json_files):
                name = json_path.name
                if progress_callback:
                    progress_callback(i, total, name)

                try:
                    # 优先使用用户指定的 file_id（冲突解决结果）
                    file_id = None
                    if file_id_override and name in file_id_override:
                        file_id = file_id_override[name]
                    elif path_mapping and name in path_mapping:
                        full_path = path_mapping[name]
                        file_id = path_based_existing.get(full_path)
                    if file_id is None:
                        file_id = existing.get(name)

                    if file_id is not None:
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
                existing, _, name_to_files = self._fetch_file_maps(project_id)
            except RuntimeError as e:
                raise RuntimeError(f"获取项目文件列表失败：{e}") from e

            # 检测同名文件冲突
            conflicts: dict[str, list[dict]] = {
                name: files for name, files in name_to_files.items()
                if len(files) > 1 or (len(files) == 1 and bool(files[0].get("folder", "")))
            }
            if conflicts:
                result.name_conflicts = conflicts

            stem = Path(filename).stem
            ext = Path(filename).suffix or ".json"
            counter = [1]  # 可变计数器，仅在成功上传后递增

            if progress_callback:
                progress_callback(0, 1, f"正在上传 {filename}…")

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
