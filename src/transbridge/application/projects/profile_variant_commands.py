"""Persist converted text and its exact terminology association in one activation."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.terminology_profiles.entry_projection import ProfileEntryInput, ProfileEntryProjector
from transbridge.application.terminology_profiles.models import TerminologyProfileSelection
from transbridge.persistence.v2.ids import VariantId, VariantRef
from transbridge.persistence.v2.models import LoadedRecord
from transbridge.persistence.v2.variant import VariantAggregate, VariantSnapshot

from .catalog import project_with_added_variant, variant_catalog
from .models import ActiveProject, LifecycleActivation, TerminologySelectionMutation, project_with_active_variant


class ProfileVariantCommands:
    def __init__(self, lifecycle, catalog, projects, variants, baselines, terminology, *, entry_inputs=None):
        self._lifecycle, self._catalog = lifecycle, catalog
        self._projects, self._variants, self._baselines = projects, variants, baselines
        self._terminology, self._entry_inputs = terminology, entry_inputs

    def targets(self, context, target_profile):
        try:
            if self._terminology is None:
                _fail("PROFILE_VARIANT_UNAVAILABLE", "版本译名方案保存服务未配置。")
            active = self._lifecycle.active
            if active is None or active.formal_variant_ref is None:
                _fail("ACTIVE_VARIANT_REQUIRED", "请先打开一个工程翻译版本。")
            self._context(active, context)
            project_id = active.project_ref.identity.value
            output = []
            with self._projects.mutation_lock:
                self._exact_profile(project_id, target_profile)
                for descriptor in variant_catalog(active.project):
                    if descriptor.variant_id == active.formal_variant_ref.identity.value:
                        continue
                    selection = self._terminology.selection(project_id, descriptor.variant_id)
                    if not _same_scheme(selection, target_profile):
                        continue
                    ref = VariantRef(VariantId(descriptor.variant_id), active.project_ref.identity)
                    record = self._variants.read_snapshot_record(ref)
                    if not isinstance(record, LoadedRecord):
                        _fail("TARGET_VARIANT_UNAVAILABLE", "目标版本不可读取，请重新打开工程。")
                    output.append({**descriptor.to_dict(), "revision": record.value.envelope.revision})
            return OperationResult.completed(tuple(output), run_id=context.run_id)
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def save(
        self,
        name,
        context,
        *,
        target_profile,
        source_profile,
        target_variant_id=None,
        expected_target_revision=None,
        expected_project_revision=None,
        expected_variant_revision=None,
        base_snapshot=None,
    ):
        descriptor = None

        def prepare(active):
            nonlocal descriptor
            project_record, source_record = self._catalog.checked_active(active, context)
            project_id = active.project_ref.identity.value
            source_id = active.formal_variant_ref.identity.value
            source = active.variant.snapshot()
            if expected_project_revision is not None and active.project.envelope.revision != expected_project_revision:
                _fail("ACTIVE_PROJECT_REVISION_CHANGED", "工程内容已变化，请重新预览后保存。")
            if expected_variant_revision is not None and source.revision != expected_variant_revision:
                _fail("ACTIVE_VARIANT_REVISION_CHANGED", "当前译文已变化，请重新预览后保存。")
            self._exact_profile(project_id, source_profile)
            self._exact_profile(project_id, target_profile)
            source_selection = self._terminology.selection(project_id, source_id)
            if not _matches(source_selection, source_profile):
                _fail("SOURCE_PROFILE_CHANGED", "当前版本的实际译名方案已变化，请重新预览。")
            if target_variant_id == source_id:
                _fail("SOURCE_VARIANT_OVERWRITE_REFUSED", "请另存为独立版本，不能覆盖来源版本。")
            if target_variant_id is None:
                ref = self._catalog.allocate_ref(active)
                project = project_with_added_variant(active.project, ref, name)
                persisted_revision, target_hash, target_selection = None, None, None
                revision = 0
            else:
                if expected_target_revision is None:
                    _fail("TARGET_VARIANT_REVISION_REQUIRED", "覆盖前请重新读取目标版本并确认覆盖。")
                if target_variant_id not in {item.variant_id for item in variant_catalog(active.project)}:
                    _fail("VARIANT_NOT_FOUND", "要覆盖的版本已不存在，请重新选择。")
                ref = VariantRef(VariantId(target_variant_id), active.project_ref.identity)
                target = self._variants.read_snapshot_record(ref)
                if not isinstance(target, LoadedRecord) or target.value.envelope.revision != expected_target_revision:
                    _fail("TARGET_VARIANT_REVISION_CHANGED", "目标版本已变化，请重新选择并确认覆盖。")
                persisted_revision = target.value.envelope.revision
                target_hash = target.source_hash
                target_selection = self._terminology.selection(project_id, target_variant_id)
                if not _same_scheme(target_selection, target_profile):
                    _fail("TARGET_PROFILE_CHANGED", "目标版本的实际方案已变化，请重新选择覆盖目标。")
                revision, project = persisted_revision + 1, active.project
            base = None
            if source_profile is None or target_profile is None:
                base = self._terminology.base_snapshot(project_id, source_id)
                if base_snapshot is not None and (
                    base_snapshot.local_project_id != project_id
                    or base_snapshot.local_variant_id != source_id
                    or (base.status, base.version_id, base.content_digest)
                    != (base_snapshot.status, base_snapshot.version_id, base_snapshot.content_digest)
                ):
                    _fail("BASE_TERMINOLOGY_CHANGED", "项目默认术语在预览后已变化，请重新预览后保存。")
                base = base if base_snapshot is None else base_snapshot
            entries = self._convert(active, source, context, target_profile, source_profile, base)
            snapshot = VariantSnapshot(ref, source.source_fingerprints, entries, revision, source.label_library)
            project = project_with_active_variant(project, ref)
            candidate = ActiveProject(
                project,
                VariantAggregate(snapshot),
                ref,
                active.persisted_project_revision,
                persisted_revision,
                source_project_hash=project_record.source_hash,
                source_variant_hash=target_hash,
                missing_inputs=active.missing_inputs,
            )
            selection = (
                None
                if target_profile is None
                else TerminologyProfileSelection(
                    project_id,
                    ref.identity.value,
                    target_profile.profile_id,
                    target_profile.revision,
                    datetime.now(UTC).isoformat(),
                )
            )
            activation = replace(
                LifecycleActivation.capture(active, candidate),
                candidate_baselines=self._baselines.provide(active.project, active.formal_variant_ref, context),
                checked_variants=(
                    (active.formal_variant_ref, source_record.value.envelope.revision, source_record.source_hash),
                ),
                terminology_selections=(
                    TerminologySelectionMutation(project_id, source_id, source_selection, source_selection),
                    TerminologySelectionMutation(project_id, ref.identity.value, target_selection, selection),
                ),
            )
            descriptor = next(item for item in variant_catalog(project) if item.variant_id == ref.identity.value)
            return candidate, activation

        if self._terminology is None:
            return OperationResult.from_exception(
                DomainError(ErrorCategory.PREREQUISITE, "PROFILE_VARIANT_UNAVAILABLE", "版本译名方案保存服务未配置。"),
                run_id=context.run_id,
            )
        result = self._lifecycle.commit_catalog_change(prepare, context)
        if not result.is_success:
            return result
        return OperationResult.completed(descriptor.to_dict(), diagnostics=result.diagnostics, run_id=context.run_id)

    def _convert(self, active, source, context, target, source_profile, base):
        projector = ProfileEntryProjector(target, base, source_profile=source_profile)
        inputs = {} if self._entry_inputs is None else self._entry_inputs(active.project, source, context)
        output = []
        for state in source.entries:
            if state.tombstone or not state.translation:
                output.append(state)
                continue
            original = state.entry_key.original
            entry_input = inputs.get(state.entry_key)
            if entry_input is not None:
                original = entry_input.original
            if original is None:
                _fail("PROFILE_VARIANT_ORIGINAL_REQUIRED", "版本仍有缺少原文的词条，请补充来源后重试。")
            result = projector.project_input(
                entry_input or ProfileEntryInput(state.entry_key.serialize(), original, state.translation, None)
            )
            if result.diagnostics:
                _fail("PROFILE_VARIANT_CONVERSION_INCOMPLETE", f"译名方案转换未完成：{result.diagnostics[0].message}")
            output.append(
                state
                if result.translation == state.translation
                else replace(state, translation=result.translation, revision=state.revision.next())
            )
        return tuple(output)

    def _exact_profile(self, project_id, profile):
        if profile is not None and (
            profile.project_id != project_id
            or self._terminology.published_revision(project_id, profile.profile_id, profile.revision) != profile
        ):
            _fail("PROFILE_REVISION_UNAVAILABLE", "预览使用的译名方案版本已不可读取，请重新预览。")

    @staticmethod
    def _context(active, context):
        if context.project_id is not None and context.project_id != active.project_ref.identity.value:
            _fail("PROJECT_CONTEXT_MISMATCH", "工程已切换，请重新打开保存窗口。")
        if context.variant_id is not None and context.variant_id != active.formal_variant_ref.identity.value:
            _fail("VARIANT_CONTEXT_MISMATCH", "翻译版本已切换，请重新打开保存窗口。")


def _same_scheme(selection, profile):
    return (
        selection is None if profile is None else selection is not None and selection.profile_id == profile.profile_id
    )


def _matches(selection, profile):
    return _same_scheme(selection, profile) and (profile is None or selection.revision == profile.revision)


def _fail(code, message):
    raise DomainError(ErrorCategory.CONFLICT, code, message)
