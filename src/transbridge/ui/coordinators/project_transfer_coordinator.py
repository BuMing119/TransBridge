from pathlib import Path, Path as PathLib

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from transbridge.persistence import (
    PERSISTENCE_ROOT,
    ProjectHandle,
    VariantStore,
    WorkspaceState,
    workspace_path,
)


class ProjectTransferCoordinator:
    """Own one application-shell interaction slice."""

    def __init__(self, host) -> None:
        self._host = host

    def save_snapshot(self):
        """另存为快照。"""
        proj = self._host.context.active_project
        vs = self._host.context.variant_store
        if not proj or not vs:
            return
        from PyQt6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self._host, "另存为快照", "快照名称:")
        if not ok or not name.strip():
            return
        snap_dir = proj.variant_dir(self._host.context.active_variant) / "snapshots"
        self._host.start_foreground_task(
            lambda: vs.save_snapshot(snap_dir, name.strip()),
            message="正在保存快照…",
            on_result=lambda dest: QMessageBox.information(
                self._host,
                "快照已保存",
                f"快照已保存到:\n{dest}",
            ),
            on_error=lambda error: QMessageBox.warning(self._host, "快照保存失败", error),
        )

    def load_snapshot(self):
        """加载快照。"""
        proj = self._host.context.active_project
        if not proj:
            return
        variant_name = self._host.context.active_variant or proj.active_variant
        snap_dir = proj.variant_dir(variant_name) / "snapshots"

        def _choose_snapshot(snapshots) -> None:
            if not snapshots:
                QMessageBox.information(self._host, "无快照", "当前版本无可用快照。")
                return
            items = [f"{item['name']} ({item['updated'][:19]})" for item in snapshots]
            from PyQt6.QtWidgets import QInputDialog

            choice, ok = QInputDialog.getItem(self._host, "加载快照", "选择快照:", items, 0, False)
            if not ok:
                return
            snap_path = PathLib(snapshots[items.index(choice)]["path"])
            mb = QMessageBox(
                QMessageBox.Icon.Warning,
                "确认加载",
                "加载快照将覆盖当前版本数据。\n建议先保存当前修改。\n是否继续？",
                parent=self._host,
            )
            btn_save = mb.addButton("保存后加载", QMessageBox.ButtonRole.AcceptRole)
            btn_load = mb.addButton("直接加载", QMessageBox.ButtonRole.DestructiveRole)
            btn_cancel = mb.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            mb.exec()
            clicked = mb.clickedButton()
            if clicked == btn_cancel:
                return

            def _load() -> None:
                self._host.start_foreground_task(
                    lambda: VariantStore.load_snapshot(snap_path),
                    message="正在加载快照…",
                    on_result=lambda store: self._host.variant_coordinator.switch_to_variant(proj, variant_name, store),
                    on_error=lambda error: QMessageBox.warning(self._host, "快照加载失败", error),
                )

            if clicked == btn_save:
                self._host.save_current_project_async(on_finished=lambda saved: saved and _load())
            elif clicked == btn_load:
                _load()

        self._host.start_foreground_task(
            lambda: VariantStore.list_snapshots(snap_dir),
            message="正在读取快照列表…",
            on_result=_choose_snapshot,
            on_error=lambda error: QMessageBox.warning(self._host, "快照读取失败", error),
        )

    def export_transbridge(self):
        """导出项目为 .transbridge ZIP 文件。"""
        proj = self._host.context.active_project
        if not proj:
            QMessageBox.warning(self._host, "导出", "请先打开一个项目。")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "导出 .transbridge", f"{proj.name}.transbridge", "TransBridge 项目 (*.transbridge);;所有文件 (*)"
        )
        if not path:
            return

        proj_dir = proj.project_dir

        def _export() -> str:
            import zipfile

            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                for source in proj_dir.rglob("*.json"):
                    archive.write(source, str(source.relative_to(proj_dir)))
            return path

        def _start_export(saved: bool) -> None:
            if not saved:
                return
            self._host.start_foreground_task(
                _export,
                message="正在导出项目包…",
                on_result=lambda target: QMessageBox.information(
                    self._host,
                    "导出完成",
                    f"项目「{proj.name}」已导出到:\n{target}",
                ),
                on_error=lambda error: QMessageBox.warning(self._host, "导出失败", error),
            )

        self._host.save_current_project_async(on_finished=_start_export)

    def import_transbridge(self, path: str | None = None):
        """导入 .transbridge 文件。"""
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self._host, "导入 .transbridge", "", "TransBridge 项目 (*.transbridge);;所有文件 (*)"
            )
        if not path:
            return

        def _inspect_archive():
            import json
            import shutil
            import zipfile

            from transbridge.persistence._utils import validate_name

            with zipfile.ZipFile(path, "r") as archive:
                if "project.json" not in archive.namelist():
                    raise ValueError("无效的 .transbridge 文件：缺少 project.json")
                for member in archive.namelist():
                    member_path = Path(member)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise ValueError(f".transbridge 包含非法路径: {member}")
                total_size = sum(info.file_size for info in archive.infolist())
                project_data = json.loads(archive.read("project.json").decode("utf-8"))
                project_name = validate_name(project_data.get("name", ""))
                destination = PERSISTENCE_ROOT / project_name
                free_bytes = shutil.disk_usage(destination.parent).free
                reserve = max(64 * 1024 * 1024, total_size // 20)
                if total_size + reserve > free_bytes:
                    required_gib = (total_size + reserve) / (1024**3)
                    free_gib = free_bytes / (1024**3)
                    raise ValueError(
                        f"目标磁盘空间不足：至少需要 {required_gib:.1f} GiB，当前可用 {free_gib:.1f} GiB。"
                    )
                return project_name, destination

        def _confirm_and_import(info) -> None:
            project_name, destination = info
            if destination.exists():
                answer = QMessageBox.question(
                    self._host,
                    "项目已存在",
                    f"项目「{project_name}」已存在。\n覆盖将丢失现有数据，是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return

            def _extract_and_load():
                import zipfile

                with zipfile.ZipFile(path, "r") as archive:
                    archive.extractall(destination)
                project = ProjectHandle.load(destination / "project.json")
                variant_store = None
                variant_name = project.active_variant
                if variant_name and project.has_variant(variant_name):
                    variant_store = VariantStore.load(project.variant_dir(variant_name) / "current.json")
                return project_name, destination, project, variant_store

            def _activate_imported(result) -> None:
                imported_name, destination, project, variant_store = result
                workspace = self._host.context.workspace or WorkspaceState.load(workspace_path())
                workspace.add_project(imported_name, destination / "project.json")
                workspace.save()
                self._host.context.workspace = workspace
                self._host.context.active_project = project
                self._host.context.active_variant = project.active_variant
                self._host.context.variant_store = variant_store
                if variant_store is not None and self._host.context.collection:
                    variant_store.apply_to(list(self._host.context.collection))
                QMessageBox.information(
                    self._host,
                    "导入完成",
                    f"项目「{imported_name}」已导入。\n请通过文件菜单解析源文件。",
                )

            self._host.start_foreground_task(
                _extract_and_load,
                message="正在解压并加载项目包…",
                on_result=_activate_imported,
                on_error=lambda error: QMessageBox.warning(self._host, "导入失败", error),
            )

        self._host.start_foreground_task(
            _inspect_archive,
            message="正在校验项目包…",
            on_result=_confirm_and_import,
            on_error=lambda error: QMessageBox.warning(self._host, "导入失败", error),
        )

    def _on_report_entry_activated(self, entry_id: str):
        """报告对话框中双击条目后跳转到Step2定位。"""
        if not self._host.context.collection:
            self._host.statusBar().showMessage("请先加载翻译集合", 5000)
            return
        entry = self._host.context.collection.get(entry_id)
        if entry is None:
            self._host.statusBar().showMessage(f"条目不存在或已被删除: {entry_id}", 5000)
            return
        # 切换到工作台 tab
        self._host.mode_tabs.setCurrentIndex(0)  # 工作台在 index 0
        self._host.workbench.locate_entry(entry_id)
