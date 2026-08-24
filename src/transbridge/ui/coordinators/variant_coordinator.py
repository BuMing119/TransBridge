from pathlib import Path as PathLib

from PyQt6.QtWidgets import QMessageBox

from transbridge.persistence import ProjectHandle, VariantStore


class VariantCoordinator:
    """Own one application-shell interaction slice."""

    def __init__(self, host) -> None:
        self._host = host

    def new_variant(self):
        """创建空白新版本。"""
        if self._host.context.uses_authoritative_projection:
            from PyQt6.QtWidgets import QInputDialog

            name, ok = QInputDialog.getText(self._host, "新建版本", "版本名称:")
            if not ok or not name.strip():
                return
            display_name = name.strip()

            def _create() -> None:
                self._host.start_foreground_task(
                    lambda: self._host.project_commands.create_variant(
                        display_name,
                        self._host.runtime_context,
                    ),
                    message=f"正在创建版本「{display_name}」…",
                    on_result=lambda result: self._finish_v2_variant_operation(
                        result,
                        success_message=f"版本「{display_name}」已创建",
                        reload_source=True,
                    ),
                )

            if self._host.context.dirty:
                self._host.save_current_project_async(on_finished=lambda saved: saved and _create())
            else:
                _create()
            return
        proj = self._host.context.active_project
        if not proj:
            return
        from PyQt6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self._host, "新建版本", "版本名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if proj.has_variant(name):
            QMessageBox.warning(self._host, "冲突", f"版本「{name}」已存在")
            return

        def _create_variant():
            proj.add_variant(name)
            proj.save()
            vs_dir = proj.variant_dir(name)
            vs_dir.mkdir(parents=True, exist_ok=True)
            vs = VariantStore(vs_dir / "current.json")
            vs.save()
            return vs

        self._host.save_current_project_async(
            on_finished=lambda saved: (
                saved
                and self._host.start_foreground_task(
                    _create_variant,
                    message="正在创建版本…",
                    on_result=lambda vs: self.switch_to_variant(proj, name, vs),
                )
            )
        )

    def copy_variant(self):
        """从当前版本复制创建新版本。"""
        if self._host.context.uses_authoritative_projection:
            from PyQt6.QtWidgets import QInputDialog

            name, ok = QInputDialog.getText(self._host, "复制版本", "新版本名称:")
            if not ok or not name.strip():
                return
            display_name = name.strip()

            def _copy() -> None:
                self._host.start_foreground_task(
                    lambda: self._host.project_commands.create_variant(
                        display_name,
                        self._host.runtime_context,
                        copy_active=True,
                    ),
                    message=f"正在复制版本「{display_name}」…",
                    on_result=lambda result: self._finish_v2_variant_operation(
                        result,
                        success_message=f"版本「{display_name}」已复制",
                        reload_source=True,
                    ),
                )

            if self._host.context.dirty:
                self._host.save_current_project_async(on_finished=lambda saved: saved and _copy())
            else:
                _copy()
            return
        proj = self._host.context.active_project
        if not proj:
            return
        from PyQt6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self._host, "复制版本", "新版本名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if proj.has_variant(name):
            QMessageBox.warning(self._host, "冲突", f"版本「{name}」已存在")
            return

        source_name = self._host.context.active_variant or proj.active_variant
        source_store = self._host.context.variant_store

        def _copy_variant():
            proj.add_variant(name, copied_from=source_name)
            proj.save()
            vs_dir = proj.variant_dir(name)
            vs_dir.mkdir(parents=True, exist_ok=True)
            new_vs = VariantStore(vs_dir / "current.json")
            if source_store:
                new_vs.translations = dict(source_store.translations)
                new_vs.labels = {key: set(value) for key, value in source_store.labels.items()}
                new_vs.label_library = dict(source_store.label_library)
                new_vs.entry_states = dict(source_store.entry_states)
            new_vs.save()
            return new_vs

        self._host.save_current_project_async(
            on_finished=lambda saved: (
                saved
                and self._host.start_foreground_task(
                    _copy_variant,
                    message="正在复制版本…",
                    on_result=lambda vs: self.switch_to_variant(proj, name, vs),
                )
            )
        )

    def switch_variant(self, name: str):
        """从 ProjectBar 下拉切换版本。"""
        if self._host.context.uses_authoritative_projection:
            if self._host.project_commands is None or self._host.runtime_context is None:
                self._host.show_message("V2 项目版本服务不可用。")
                return
            from transbridge.application.projects import DirtyDecision
            from transbridge.persistence.v2 import ProjectId, ProjectRef, VariantId, VariantRef

            project_id = self._host.context.active_project_id
            if project_id is None:
                self._host.show_message("没有活动项目。")
                return
            display_name = next(
                (str(item["name"]) for item in self._host.context.project_variants if item["id"] == name),
                name,
            )
            decision = None
            if self._host.context.dirty:
                answer = QMessageBox.question(
                    self._host,
                    "保存确认",
                    "当前版本有未保存修改。切换前是否保存？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                )
                if answer == QMessageBox.StandardButton.Cancel:
                    self._host.workbench.project_bar.refresh()
                    return
                if answer == QMessageBox.StandardButton.Yes:
                    self._host.save_current_project_async(
                        on_finished=lambda saved: (
                            self.switch_variant(name) if saved else self._host.workbench.project_bar.refresh()
                        )
                    )
                    return
                decision = DirtyDecision.DISCARD
            project_ref = ProjectRef(ProjectId(project_id))
            variant_ref = VariantRef(VariantId(name), project_ref.identity)
            started = self._host.start_foreground_task(
                lambda: self._host.project_commands.switch_v2(
                    project_ref,
                    variant_ref,
                    self._host.runtime_context,
                    dirty_decision=decision,
                ),
                message=f"正在加载版本「{display_name}」…",
                on_result=lambda result: self._finish_v2_variant_operation(
                    result,
                    success_message=f"已切换到版本「{display_name}」",
                    reload_source=True,
                ),
            )
            if not started:
                self._host.workbench.project_bar.refresh()
            return
        proj = self._host.context.active_project
        if not proj or not proj.has_variant(name):
            return
        should_save = True
        # 检查脏标记
        if self._host.context.variant_store and self._host.context.variant_store.dirty:
            ws = self._host.context.workspace
            behavior = ws.settings.get("save_behavior", "prompt") if ws else "prompt"
            if behavior == "prompt":
                ret = QMessageBox.question(
                    self._host,
                    "保存确认",
                    f"当前版本「{self._host.context.active_variant}」有未保存的修改。\n是否保存后切换？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                )
                if ret == QMessageBox.StandardButton.Cancel:
                    return
                should_save = ret == QMessageBox.StandardButton.Yes

        def _load_variant() -> None:
            vs_path = proj.variant_dir(name) / "current.json"
            self._host.start_foreground_task(
                lambda: VariantStore.load(vs_path),
                message=f"正在加载版本「{name}」…",
                on_result=lambda vs: self.switch_to_variant(proj, name, vs),
            )

        if should_save:
            self._host.save_current_project_async(on_finished=lambda saved: saved and _load_variant())
        else:
            _load_variant()

    def manual_save(self):
        """手动保存当前版本数据（Ctrl+S / 工具栏按钮）。"""
        if self._host.context.uses_authoritative_projection:
            if self._host.project_commands is None or self._host.runtime_context is None:
                self._host.show_message("V2 Project command adapter is unavailable.")
                return
        elif self._host.context.variant_store is None:
            self._host.show_message("无活跃版本，无需保存")
            return
        self._host.save_current_project_async()

    def _activate_legacy_project(self, path: str) -> None:
        project = ProjectHandle.load(PathLib(path))
        if not project.name or not project.active_variant:
            self._host.show_message("Legacy Project metadata is unavailable or has no active Variant.")
            return
        self._activate_legacy_variant(str(project.config_path), project.active_variant)

    def _activate_legacy_variant(self, project_key: str, variant_name: str) -> None:
        if self._host.project_commands is None or self._host.runtime_context is None:
            self._host.show_message("V2 Project command adapter is unavailable.")
            return
        from transbridge.application.projects import DirtyDecision

        decision = None
        if self._host.context.dirty:
            ret = QMessageBox.question(
                self._host,
                "保存确认",
                "The active V2 Variant has unpersisted revisions. Save before switching?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if ret == QMessageBox.StandardButton.Cancel:
                return
            decision = DirtyDecision.SAVE if ret == QMessageBox.StandardButton.Yes else DirtyDecision.DISCARD
        result = self._host.project_commands.switch_legacy(
            project_key,
            variant_name,
            self._host.runtime_context,
            dirty_decision=decision,
        )
        if not result.is_success:
            diagnostic = result.diagnostics[0]
            self._host.show_message(f"{diagnostic.code}: {diagnostic.message}")
            return
        self._host.legacy_mapping_key = project_key
        self._host.show_message("V2 Project/Variant activated after mapping and baseline validation.")

    def switch_to_variant(self, proj: ProjectHandle, name: str, vs: VariantStore) -> None:
        """切换到指定版本并刷新 UI。"""
        self._host.context.active_variant = name
        proj.active_variant = name
        proj.save()
        self._host.context.variant_store = vs
        if self._host.context.collection:
            vs.apply_to(list(self._host.context.collection))
        self._host.context.variant_changed.emit(name)

    def delete_variant(self, name: str):
        """删除指定版本（至少保留一个）。"""
        if self._host.context.uses_authoritative_projection:
            display_name = next(
                (str(item["name"]) for item in self._host.context.project_variants if item["id"] == name),
                name,
            )
            if len(self._host.context.project_variants) <= 1:
                QMessageBox.warning(self._host, "无法删除", "至少保留一个版本。")
                return
            answer = QMessageBox.question(
                self._host,
                "确认删除",
                f"确定要删除版本「{display_name}」吗？\n此操作不可撤销。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            deleting_active = self._host.context.active_variant_id == name

            def _delete() -> None:
                self._host.start_foreground_task(
                    lambda: self._host.project_commands.delete_variant(name, self._host.runtime_context),
                    message=f"正在删除版本「{display_name}」…",
                    on_result=lambda result: self._finish_v2_variant_operation(
                        result,
                        success_message=f"版本「{display_name}」已删除",
                        reload_source=deleting_active,
                    ),
                )

            if self._host.context.dirty:
                self._host.save_current_project_async(on_finished=lambda saved: saved and _delete())
            else:
                _delete()
            return
        proj = self._host.context.active_project
        if not proj:
            return
        if len(proj.variants) <= 1:
            QMessageBox.warning(self._host, "无法删除", "至少保留一个版本。")
            return
        ret = QMessageBox.question(
            self._host,
            "确认删除",
            f"确定要删除版本「{name}」吗？\n该版本的所有快照也将被删除，此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        # 如果删除的是当前版本，先切换到其他版本
        if name == self._host.context.active_variant:
            other = next(v["name"] for v in proj.variants if v["name"] != name)
            self.switch_variant(other)
        # 删除版本目录
        import shutil

        variant_dir = proj.variant_dir(name)
        if variant_dir.exists():
            shutil.rmtree(str(variant_dir))
        proj.remove_variant(name)
        proj.save()
        self._host.workbench.project_bar.refresh()
        self._host.show_message(f"已删除版本「{name}」")

    def _finish_v2_variant_operation(
        self,
        result,
        *,
        success_message: str,
        reload_source: bool,
    ) -> None:
        if not result.is_success:
            diagnostic = result.diagnostics[0]
            self._host.show_message(f"{diagnostic.code}: {diagnostic.message}")
            QMessageBox.warning(self._host, "版本操作失败", diagnostic.message)
            self._host.workbench.project_bar.refresh()
            return
        self._host.show_message(success_message)
        if result.diagnostics:
            self._host.show_message(f"{success_message}；{result.diagnostics[0].message}")
        if reload_source:
            for source in self._host.context.project_sources:
                if source.get("type") == "esp" and source.get("path"):
                    self._host.project_coordinator.restore_parse_esp(str(source["path"]))

    def rename_project(self, new_name: str):
        """重命名项目——移动目录、更新 workspace。"""
        from transbridge.persistence._utils import validate_name

        try:
            new_name = validate_name(new_name)
        except ValueError as e:
            QMessageBox.warning(self._host, "名称无效", str(e))
            return
        proj = self._host.context.active_project
        ws = self._host.context.workspace
        if not proj or not ws:
            return
        old_name = proj.name
        old_dir = proj.project_dir
        new_dir = old_dir.parent / new_name
        if new_dir.exists():
            QMessageBox.warning(self._host, "冲突", f"项目「{new_name}」已存在")
            return
        # 移动目录
        import shutil

        shutil.move(str(old_dir), str(new_dir))
        # 更新 project.json
        new_config_path = new_dir / "project.json"
        proj._data["name"] = new_name
        proj._path = new_config_path
        proj.save()
        # 更新 workspace
        ws.projects.pop(old_name, None)
        ws.projects[new_name] = str(new_config_path)
        ws.active_project = new_name
        ws.save()
        self._host.context.active_project = proj
        self._host.workbench.project_bar.refresh()
        self._host.show_message(f"项目已重命名: {old_name} -> {new_name}")
