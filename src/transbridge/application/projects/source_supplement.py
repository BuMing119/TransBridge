"""Preview and publish explicitly supplied content without deleting saved translations."""

from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
import secrets
from threading import RLock

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.io import FormatId
from transbridge.application.io.identity import SourceNamespace
from transbridge.persistence.v2.ids import ProjectId, ProjectRef
from transbridge.persistence.v2.models import ProjectDto
from transbridge.persistence.v2.schema import parse_json_bytes

from .provisioning import ProjectSourceRequest
from .source_content import source_content_identity
from .source_input_publication import PreparedSourcePublication, publish_source_input
from .source_registry import SourceRegistrySnapshot, migrate_legacy_source_registry
from .source_supplement_mapping import remap_rows, supplement_rows, supplement_variants
from .source_supplement_models import SourceSupplementPreview


@dataclass(frozen=True)
class _SupplementDraft:
    publication: PreparedSourcePublication
    rows: tuple
    choices: tuple


class ProjectSourceSupplementService:
    """A chosen source and explicit row mapping supply only missing operation inputs."""

    def __init__(self, lifecycle, projects, store, baselines, preparer):
        self._lifecycle, self._projects, self._store = lifecycle, projects, store
        self._baselines, self._preparer = baselines, preparer
        self._pending = {}
        self._lock = RLock()

    def active_project_path(self, context):
        active = self._lifecycle.active
        if active is None:
            return None
        self._check_context(active.project_ref, context)
        return self._projects.path_for(active.project_ref)

    def prepare(self, project_path, source_id, namespace, replacement_path, context, *, format_id=None):
        try:
            ref = self._resolve(project_path, context)
            active = self._lifecycle.active
            if active is not None and active.project_ref == ref and active.dirty:
                raise ValueError("请先保存当前译文，再补充输入。")
            generation = self._lifecycle.generation
            expected = self._store.read(ref)
            registry = SourceRegistrySnapshot.from_project_data(expected.project.envelope.data)
            source = next((item for item in registry.sources if item.source_id == source_id), None)
            if source_id is not None and source is None:
                raise ValueError("所选来源已经变化，请重新选择要补充的来源。")
            retained = self._retained_source(source, expected.variants)
            options = {} if source is None else dict(source.format_options)
            if retained is not None:
                options.pop("source_namespace", None)
            request = ProjectSourceRequest(
                str(Path(replacement_path).resolve(strict=True)),
                source.format_id if source is not None else FormatId(format_id) if format_id else None,
                options=tuple(options.items()),
            )
            prepared = self._preparer.prepare_source(request, context, role="primary", common_options=())
            rows, choices = supplement_rows(
                expected.variants,
                prepared,
                namespace,
                active_variant_id=expected.project.envelope.data.get("active_variant_id"),
                covered_keys=() if retained is None else {row.entry_key for row in retained.hydration.entries},
            )
            project = _supplemented_project(expected.project, registry, source if retained is None else None, prepared)
            token = secrets.token_hex(24)
            preview = _preview(token, prepared, expected, rows, choices)
            publication = PreparedSourcePublication(
                context.owner_id,
                generation,
                expected,
                project,
                supplement_variants(expected.variants, prepared, rows, namespace),
                replace(request, expected_fingerprint=prepared.baseline.fingerprint.sha256),
                prepared,
                SourceNamespace(namespace) if namespace else None,
                str(self._projects.path_for(ref)),
                preview,
            )
            with self._lock:
                self._pending[token] = _SupplementDraft(publication, rows, choices)
            return OperationResult.completed(preview, run_id=context.run_id)
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def remap(self, token, mappings, context):
        try:
            with self._lock:
                draft = self._get(token, context)
                candidate = draft.publication
                rows = remap_rows(draft.rows, draft.choices, mappings)
                preview = _preview(token, candidate.source, candidate.expected, rows, draft.choices)
                candidate = replace(
                    candidate,
                    preview=preview,
                    variants=supplement_variants(
                        candidate.expected.variants,
                        candidate.source,
                        rows,
                        getattr(candidate.old_namespace, "value", None),
                    ),
                )
                self._pending[token] = _SupplementDraft(candidate, rows, draft.choices)
            return OperationResult.completed(preview, run_id=context.run_id)
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def commit(self, token, context):
        try:
            with self._lock:
                candidate = self._get(token, context).publication
                del self._pending[token]
            return publish_source_input(
                candidate,
                token,
                context,
                lifecycle=self._lifecycle,
                store=self._store,
                baselines=self._baselines,
                preparer=self._preparer,
            )
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def discard(self, token, context):
        with self._lock:
            draft = self._pending.get(token)
            if draft is not None and draft.publication.owner == context.owner_id:
                del self._pending[token]

    def _get(self, token, context):
        draft = self._pending.get(token)
        if draft is None:
            raise ValueError("补充输入的预览已失效，请重新选择文件。")
        if draft.publication.owner != context.owner_id:
            raise DomainError(ErrorCategory.PERMISSION, "SOURCE_UPDATE_OWNER_MISMATCH", "预览不属于当前操作。")
        self._check_context(ProjectRef(ProjectId(draft.publication.project.envelope.identity)), context)
        return draft

    def _resolve(self, path, context):
        selected = Path(path).resolve(strict=True)
        document = parse_json_bytes(selected.read_bytes())
        ref = ProjectRef(ProjectId(str(document["id"])))
        if selected != Path(self._projects.path_for(ref)).resolve():
            raise ValueError("请选择当前数据目录中登记的工程。")
        self._check_context(ref, context)
        return ref

    def _retained_source(self, source, variants):
        """Keep any already usable source input while supplying uncovered rows."""
        if source is None or source.source_artifact_id is None:
            return None
        load_saved = getattr(self._preparer, "load_saved_source", None)
        if load_saved is None:
            return None
        try:
            saved = load_saved(source.to_dict())
        except (OSError, DomainError) as exc:
            if isinstance(exc, DomainError) and exc.category is not ErrorCategory.PREREQUISITE:
                raise
            # Availability is represented by the existing missing-input state;
            # the explicitly selected new input is the repair for this failure.
            return None
        if saved.hydration is not None and all(
            saved.baseline.fingerprint in item.source_fingerprints for item in variants
        ):
            return saved
        return None

    @staticmethod
    def _check_context(ref, context):
        if context.project_id is not None and context.project_id != ref.identity.value:
            raise DomainError(ErrorCategory.PERMISSION, "PROJECT_CONTEXT_MISMATCH", "补充目标与请求工程不一致。")


def _preview(token, prepared, expected, rows, choices):
    matched = sum(row.matched_key is not None for row in rows)
    matched_keys = {row.matched_key for row in rows if row.matched_key is not None}
    return SourceSupplementPreview(
        token,
        prepared.hydration.source_snapshot.source.display_name or Path(prepared.hydration.location).name,
        prepared.hydration.location,
        len(expected.variants),
        matched,
        len(rows) - matched,
        sum(choice.key not in matched_keys for choice in choices),
        rows,
        choices,
    )


def _supplemented_project(project, registry, source, prepared):
    if source is None:
        matching = tuple(
            item
            for item in registry.sources
            if source_content_identity(item.to_dict()) == prepared.baseline.fingerprint.namespace.value
        )
        if len(matching) == 1:
            source = matching[0]
    if any(
        (source is None or item.source_id != source.source_id)
        and source_content_identity(item.to_dict()) == prepared.baseline.fingerprint.namespace.value
        for item in registry.sources
    ):
        raise DomainError(
            ErrorCategory.CONFLICT,
            "PROJECT_SOURCE_IDENTITY_DUPLICATE",
            "所选输入属于工程中另一个已有来源，请选择该来源，或为当前来源选择对应文件。",
        )
    registration = migrate_legacy_source_registry(project.envelope.identity, (prepared.to_dict(),)).sources[0]
    registration = replace(
        registration,
        source_id=source.source_id
        if source is not None
        else f"src-{sha256(prepared.baseline.fingerprint.namespace.value.encode()).hexdigest()[:24]}",
        display_name=source.display_name if source and source.display_name else registration.display_name,
        enabled=source.enabled if source else True,
        plugin_scope=source.plugin_scope if source else registration.plugin_scope,
    )
    sources = tuple(item for item in registry.sources if item.source_id != registration.source_id) + (registration,)
    data = deepcopy(project.envelope.data)
    data.update(SourceRegistrySnapshot(sources, registry.relations, registry.diagnostics).to_project_data())
    if isinstance(data.get("legacy"), dict):
        data["legacy"].pop("archive_recovery", None)
    return ProjectDto(replace(project.envelope, revision=project.envelope.revision + 1, data=data))
