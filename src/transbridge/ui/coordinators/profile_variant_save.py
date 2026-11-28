# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Save a temporary naming-scheme preview as an independently editable Variant."""

from PyQt6.QtWidgets import QInputDialog, QMessageBox

from transbridge.config.dictionary_preferences import dictionary_preferences


class ProfileVariantSaveCoordinator:
    def __init__(self, host):
        self._host = host

    def request(self) -> bool:
        """Return whether a preview owns this explicit user save request."""
        controller = getattr(self._host.workbench, "terminology_profile_controller", None)
        if controller is None:
            return False
        if controller.refresh_pending:
            self._host.show_message("译名方案正在读取，请稍后再保存。")
            return True
        if not controller.state.is_preview:
            return False
        try:
            projection = controller.capture_write_projection()
        except RuntimeError as exc:
            self._host.show_message(str(exc))
            return True
        identity = (projection.project_id, projection.variant_id)

        def prepare():
            if self._host.context.active_version_identity != identity:
                self._host.show_message("当前翻译版本已经变化，请重新选择方案后保存。")
                return
            context = self._host.runtime_context
            revisions = (self._host.context.project_revision, self._host.context.variant_revision)
            self._host.start_foreground_task(
                lambda: self._host.project_commands.profile_variant_targets(context, projection.profile),
                message="正在读取对应方案的翻译版本…",
                on_result=lambda result: self._choose(result, projection, identity, revisions),
            )

        if self._host.context.dirty:
            self._host.save_current_project_async(on_finished=lambda saved: saved and prepare())
        else:
            prepare()
        return True

    def _choose(self, result, projection, identity, revisions):
        if not result.is_success:
            self._failed(result)
            return
        if self._host.context.active_version_identity != identity:
            self._host.show_message("当前翻译版本已经变化，请重新保存。")
            return
        targets = tuple(item for item in result.value if item["id"] != identity[1])
        target = None
        if targets:
            choices = ["另存为新的翻译版本", *(f"覆盖「{item['name']}」" for item in targets)]
            choice, accepted = QInputDialog.getItem(self._host, "保存方案译文", "选择保存位置：", choices, 0, False)
            if not accepted:
                return
            index = choices.index(choice)
            target = targets[index - 1] if index else None
        if target is None:
            label = "项目默认译名" if projection.profile is None else projection.profile.name
            existing = {str(item["name"]).casefold() for item in self._host.context.project_variants}
            suggestion = f"{label}译文"
            suffix = 2
            while suggestion.casefold() in existing:
                suggestion = f"{label}译文 ({suffix})"
                suffix += 1
            name, accepted = QInputDialog.getText(self._host, "另存为翻译版本", "新翻译版本名称：", text=suggestion)
            if not accepted or not name.strip():
                return
            name = name.strip()
        else:
            name = target["name"]
            answer = QMessageBox.question(
                self._host,
                "覆盖翻译版本",
                f"将用当前方案的完整转换结果覆盖「{name}」的译文，包含该版本的独立修改。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        context = self._host.runtime_context
        project_revision, variant_revision = revisions
        source_key = self._host.context.active_key
        self._host.start_foreground_task(
            lambda: self._host.project_commands.save_profile_variant(
                name,
                context,
                target_profile=projection.profile,
                source_profile=projection.source_profile,
                base_snapshot=projection.base_snapshot,
                projection_projector=projection.projector,
                target_variant_id=None if target is None else target["id"],
                expected_target_revision=None if target is None else target["revision"],
                expected_project_revision=project_revision,
                expected_variant_revision=variant_revision,
            ),
            message=f"正在保存翻译版本「{name}」…",
            on_result=lambda saved: self._finished(saved, name, identity[0], source_key),
        )

    def _failed(self, result):
        diagnostic = result.diagnostics[0]
        self._host.show_message(f"{diagnostic.code}: {diagnostic.message}")
        QMessageBox.warning(self._host, "方案译文保存失败", diagnostic.message)

    def _finished(self, result, name, project_id, source_key):
        if not result.is_success:
            self._failed(result)
            return
        self._host.show_message(f"翻译版本「{name}」已保存，可独立编辑和继续翻译。")
        target = (project_id, result.value["id"])

        def publish_saved_variant():
            context = self._host.context
            if (
                context.active_version_identity == target
                and source_key in context.slots
                and dictionary_preferences(context).save_on_project_save
            ):
                # The restored target is already clean. Its save receipt binds the dictionary
                # reader to the committed target version, including its actual naming profile.
                self._host.save_current_project_async(
                    automatic=True, include_dictionary=True, dictionary_source_key=source_key
                )

        self._host.project_coordinator.restore_active_sources(on_restored=publish_saved_variant)


__all__ = ["ProfileVariantSaveCoordinator"]
