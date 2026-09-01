"""Source commands and projection publication for Assistant imports."""

from __future__ import annotations

from dataclasses import replace

from transbridge.application.io import FormatId, default_format_catalog
from transbridge.application.projects import ProjectSourceRequest
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.projection_types import CollectionSlot

from .base import ToolResult


def publish_import(target, path, label, collection, snapshot, format_id, action, options=()):
    if action == "append":
        return target.dispatch(lambda: _append(target, collection))

    def check_target():
        target.check()
        if path in target.app.slots:
            raise ValueError(f"集合「{label}」已存在；请使用 append 更新已登记的同一来源条目。")
        return target.expected() if target.authoritative else {}

    expected = target.dispatch(check_target)
    slot = CollectionSlot(
        label=label,
        collection=collection,
        source_snapshot=snapshot,
        format_id=format_id,
        esp_path=path if format_id is FormatId.PLUGIN_SSE else None,
        eet_path=path if format_id is FormatId.XML_EET else None,
        xt_path=path if format_id is FormatId.XML_XT else None,
    )
    committed = None
    if target.authoritative:
        if snapshot is None or default_format_catalog().adapter(format_id) is None:
            raise ValueError("该格式尚无正式来源适配器；内部 JSON 可用 append 更新同一 EntryKey，不能新增未登记来源。")
        # Parsing and baseline preparation remain on the calling worker. Only
        # publication of the read model below is marshalled to the Qt thread.
        result = target.app.project_commands.add_source(
            ProjectSourceRequest(path, format_id, expected_fingerprint=snapshot.sha256, options=options),
            target.app.runtime_context,
            **expected,
        )
        if not result.is_success:
            raise ValueError("；".join(item.message for item in result.diagnostics))
        committed = result.value
        from transbridge.ui.source_hydration import slot_from_hydration

        slot = slot_from_hydration(committed.hydration)

    def publish():
        if committed is not None:
            if (
                target.identity != target.app.active_version_identity
                or committed.project_revision != target.app.project_revision
                or committed.variant_revision != target.app.variant_revision
            ):
                return ToolResult(
                    success=False,
                    partial=True,
                    message="来源已登记到原工程版本；视图已变化，未覆盖当前集合。",
                    data={"registered": True, "activated": False, "label": label},
                    recovery_action="重新载入原工程版本以查看已登记来源。",
                )
        else:
            target.check()
        target.app.add_slot(path, slot)
        target.app.activate_slot(path)
        return ToolResult.ok(
            f"已创建并激活集合「{label}」",
            data={
                "action": action,
                "label": label,
                "entry_count": len(slot.collection),
                "activated": True,
            },
        )

    return target.dispatch(publish)


def _append(target, collection):
    target.check()
    slot = getattr(target.app, "active_slot", None)
    if slot is None or slot.collection is None:
        raise ValueError("当前无活跃集合；请使用 action=create_slot。")
    if target.authoritative:
        target.commit_records(collection)
    else:
        # Full EntryKeys avoid collapsing legacy keys into unrelated sources.
        merged = {entry.identity: entry for entry in slot.collection}
        for entry in collection:
            previous = merged.get(entry.identity)
            merged[entry.identity] = replace(entry, revision=previous.revision.next()) if previous else entry
        slot.collection = TranslationEntryCollection(merged.values())
        if hasattr(target.app, "mark_dirty"):
            target.app.mark_dirty()
        signal = getattr(target.app, "collection_changed", None)
        if signal is not None:
            signal.emit(slot.collection)
    return ToolResult.ok(
        "已追加条目",
        data={
            "action": "append",
            "added_count": len(collection),
            "total_count": len(slot.collection),
            "target_label": slot.label,
        },
    )
