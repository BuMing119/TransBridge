"""
ParaTranzUploader：将本地 TranslationEntryCollection 上传到 ParaTranz。

保留普通单文件上传的兼容入口；当前 GUI 上传由 application.file_upload 执行。
此入口将完整集合导出为一个 JSON，按根目录路径定位文件，并在 413 时递归拆分。

translation_mode 取值：
  "orig_only"   — 仅更新原文（默认）；新建文件正常创建，已有文件只更新原文，不碰译文
  "trans_safe"  — 仅导入译文，不覆盖已人工编辑的词条；新建文件跳过（无 file_id）
  "trans_force" — 仅导入译文，强制覆盖所有译文；新建文件跳过
  "both"        — 更新原文并安全导入译文；新建文件创建后再导入译文
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
import json
from pathlib import Path
import tempfile

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParatranzJsonAdapter, SourceDescriptor, WriteRequest
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
from transbridge.paratranz.config_manager import ParatranzConfig


@dataclass
class UploadResult:
    """上传操作的结果摘要"""

    created: int = 0  # 新建文件数
    updated: int = 0  # 更新原文文件数
    skipped: int = 0  # 因错误跳过的文件数
    translation_updated: int = 0  # 成功导入译文的文件数
    files: list[str] = field(default_factory=list)  # 成功处理的文件名列表


class ParaTranzUploader:
    def __init__(self, config: ParatranzConfig):
        self._api = ParatranzFilesAPI(token=config.token, config=config)

    def _fetch_file_maps(self, project_id: int) -> tuple[dict[str, int], dict[str, list[dict]]]:
        """
        获取项目文件列表，返回两个映射：
          - path_based:        完整路径 → file_id
          - name_to_files:     文件名 → [文件信息列表]（拒绝根目录重复路径）
        """
        import logging

        logger = logging.getLogger(__name__)

        file_list = self._api.list_files(project_id) or []
        logger.info(f"[ParaTranzUploader] 从项目 {project_id} 获取到 {len(file_list)} 个文件")

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
            full_path = f"{actual_folder}/{actual_name}" if actual_folder else actual_name
            path_based[full_path] = f["id"]

        return path_based, name_to_files

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
            translation_mode:   orig_only / trans_safe / trans_force / both，见模块说明
            progress_callback:  进度回调 (current, total, filename)

        Returns:
            UploadResult
        """
        result = UploadResult()

        if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise ValueError("普通上传文件必须位于项目根目录。")
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Map the complete source once, before any 413-driven partitioning.
            # Child files keep these exact wire records and global order values.
            source_path = Path(tmp_dir) / "source.json"
            written = ParatranzJsonAdapter().write(
                WriteRequest(
                    SourceDescriptor(str(source_path), source_path.name, media_type="application/json"),
                    FormatId.JSON_PARATRANZ,
                    tuple(collection),
                    0,
                    RequestContext("single-paratranz-upload"),
                    new_template=b"",
                    options=(("context_source_order", True),),
                )
            )
            if written.outcome is not OperationOutcome.COMPLETED:
                messages = "; ".join(diagnostic.message for diagnostic in written.diagnostics)
                raise ValueError(messages or "Unable to prepare ParaTranz JSON for upload.")
            entries = json.loads(source_path.read_text(encoding="utf-8"))
            # Saved IDs may belong to another upload layout. The fixed file's
            # own keys determine import identity; never transport foreign IDs.
            for entry in entries:
                entry.pop("id", None)

            try:
                path_based, name_to_files = self._fetch_file_maps(project_id)
            except RuntimeError as e:
                raise RuntimeError(f"获取项目文件列表失败：{e}") from e

            # A basename in another virtual directory is a different target.
            # Keep deterministic 413-derived filenames in this same root scope.
            existing = {path: file_id for path, file_id in path_based.items() if "/" not in path}
            duplicates = {
                name for name, files in name_to_files.items() if sum(not item.get("folder") for item in files) > 1
            }
            if filename in duplicates:
                raise ValueError("普通上传根目录路径存在多个文件，无法安全选择目标。")

            stem = Path(filename).stem
            ext = Path(filename).suffix or ".json"
            if any(
                name.startswith(stem + "_") and name.endswith(ext) and name[len(stem) + 1 : -len(ext)].isdigit()
                for name in duplicates
            ):
                raise ValueError("普通上传分片根目录路径存在多个文件，无法安全选择目标。")
            counter = [1]  # 可变计数器，仅在成功上传后递增

            if progress_callback:
                progress_callback(0, 1, f"正在上传 {filename}…")

            self._upload_entries_recursive(
                entries,
                stem,
                ext,
                project_id,
                existing,
                result,
                tmp_dir,
                counter,
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
        json_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

        try:
            if name in existing:
                file_id = existing[name]
                if do_reupload:
                    self._validate_root_file(project_id, file_id, name)
                    self._api.reupload_file(project_id, file_id, str(json_path))
                    result.updated += 1
                if do_trans:
                    try:
                        self._validate_root_file(project_id, file_id, name)
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
                                self._api.update_file_translation(
                                    project_id, new_file_id, str(json_path), force=force_trans
                                )
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
                    entries[:mid],
                    stem,
                    ext,
                    project_id,
                    existing,
                    result,
                    tmp_dir,
                    counter,
                    translation_mode=translation_mode,
                    is_split=True,
                )
                self._upload_entries_recursive(
                    entries[mid:],
                    stem,
                    ext,
                    project_id,
                    existing,
                    result,
                    tmp_dir,
                    counter,
                    translation_mode=translation_mode,
                    is_split=True,
                )
            else:
                result.skipped += 1
                raise RuntimeError(f"上传 {name} 失败（共 {len(entries)} 条词条）：{err}") from e

    def _validate_root_file(self, project_id: int, file_id: int, filename: str) -> None:
        info = self._api.get_file_info(project_id, file_id)
        name = str(info.get("name", "")).replace("\\", "/").strip("/") if isinstance(info, dict) else ""
        folder = str(info.get("folder") or "").replace("\\", "/").strip("/") if isinstance(info, dict) else ""
        path = f"{folder}/{name}" if folder and "/" not in name else name
        if not isinstance(info, dict) or info.get("id") != file_id or path != filename:
            raise RuntimeError("普通上传目标文件已移动或改变，未执行写入；请重新上传。")
