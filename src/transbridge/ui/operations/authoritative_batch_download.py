"""CAS-guarded publication boundary for legacy ParaTranz batch downloads."""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field

from transbridge.application.projects import EntryStatePatch
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef


@dataclass(slots=True)
class AuthoritativeBatchDownloadSession:
    context: object
    identity: tuple[str, str] | None
    project_revision: int | None
    variant_revision: int | None
    authoritative: bool
    _seen_entry_keys: set = field(default_factory=set)

    @classmethod
    def capture(cls, context: object) -> AuthoritativeBatchDownloadSession:
        authoritative = bool(getattr(context, "uses_authoritative_projection", False))
        identity = getattr(context, "active_version_identity", None)
        if authoritative and identity is None:
            raise RuntimeError("活动工程版本身份不可用；本次未修改本地内容。")
        return cls(
            context,
            identity,
            getattr(context, "project_revision", None),
            getattr(context, "variant_revision", None),
            authoritative,
        )

    def detached(self, collection) -> TranslationEntryCollection:
        if not self.authoritative:
            return collection
        candidate = TranslationEntryCollection(copy(entry) for entry in collection)
        duplicate = self._seen_entry_keys.intersection(entry.identity for entry in candidate)
        if duplicate:
            raise RuntimeError("所选来源包含重复 EntryKey，无法形成一次权威提交")
        self._seen_entry_keys.update(entry.identity for entry in candidate)
        return candidate

    def commit(self, updated_collections: list[tuple[object, TranslationEntryCollection]]) -> None:
        if not self.authoritative or not updated_collections:
            return
        if getattr(self.context, "active_version_identity", None) != self.identity:
            raise RuntimeError("活动工程或版本已变化，批量下载结果未提交")
        commands = getattr(self.context, "project_commands", None)
        runtime_context = getattr(self.context, "runtime_context", None)
        if commands is None or runtime_context is None:
            raise RuntimeError("权威 Variant 写入适配器不可用，批量下载结果未提交")
        patches = {
            entry.identity: EntryStatePatch(entry.translation or "", entry.stage, tuple(entry.external_refs))
            for _slot, collection in updated_collections
            for entry in collection
        }
        project_identity, variant_identity = self.identity
        committed = commands.replace_entry_records(
            patches,
            runtime_context,
            expected_project_revision=self.project_revision,
            expected_variant_revision=self.variant_revision,
            expected_variant_ref=VariantRef(VariantId(variant_identity), ProjectId(project_identity)),
        )
        if not committed.is_success:
            message = committed.diagnostics[0].message if committed.diagnostics else "权威 Variant 写入失败"
            raise RuntimeError(f"批量下载提交失败：{message}")

    def publish(self, updated_collections: list[tuple[object, TranslationEntryCollection]]) -> None:
        if not self.authoritative or getattr(self.context, "active_version_identity", None) != self.identity:
            return
        for slot, collection in updated_collections:
            slot.collection = collection


__all__ = ["AuthoritativeBatchDownloadSession"]
