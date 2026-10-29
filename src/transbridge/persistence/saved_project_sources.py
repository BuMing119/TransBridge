"""Persist parsed source results and validate saved references without reading live files."""

from dataclasses import replace

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.io import FormatId
from transbridge.application.projects.source_content import source_content_identity


class SavedProjectSourcePreparer:
    """One persistence boundary shared by imports, provisioning and source updates."""

    def __init__(self, preparer, artifacts):
        self._preparer = preparer
        self._artifacts = artifacts

    def prepare_source(self, request, context, *, role, common_options):
        return self.persist_prepared_source(
            self._preparer.prepare_source(request, context, role=role, common_options=common_options)
        )

    def persist_prepared_source(self, prepared):
        # Protocol-only preparers used by non-I/O callers may omit hydration;
        # only a complete parsed result can become a durable source artifact.
        if prepared.hydration is None:
            return prepared
        descriptor = prepared.to_dict()
        descriptor["source_artifact_id"] = self._artifacts.put(prepared)
        return replace(prepared, descriptor=tuple(descriptor.items()))

    def load_saved_source(self, source):
        return load_saved_project_source(self._artifacts, source, ())


def load_saved_project_source(artifacts, source, source_fingerprints):
    """Verify the registered content identity before exposing a saved read model."""
    try:
        prepared = artifacts.get(source["source_artifact_id"])
        validate_saved_source(prepared, source, source_fingerprints)
        descriptor = prepared.to_dict()
        descriptor["source_artifact_id"] = source["source_artifact_id"]
        hydration = replace(
            prepared.hydration,
            source_id=source.get("source_id"),
            reference_location=str(source.get("location") or ""),
        )
        return replace(prepared, descriptor=tuple(descriptor.items()), hydration=hydration)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # A broken reference cannot establish which companion inputs were used.
        # Preserve the saved Variant rather than silently parsing changed input.
        raise DomainError(
            ErrorCategory.PREREQUISITE,
            "PROJECT_SOURCE_ARTIFACT_UNAVAILABLE",
            "保存的来源成果不可用或与工程身份不一致；已有译文可以继续编辑和保存，需要来源内容时请补充输入。",
            cause=exc,
        ) from exc


def validate_saved_source(prepared, source, saved_fingerprints=()):
    """Require the artifact's format, parser options and content identity to agree."""
    descriptor = prepared.to_dict()
    fingerprint = prepared.baseline.fingerprint
    namespace = source_content_identity(source)
    options = dict(source.get("format_options") or {})
    expected_options = dict(descriptor.get("format_options") or {})
    format_id = FormatId(source["format_id"])
    options.setdefault("skip_empty", False)
    if format_id is FormatId.PLUGIN_SSE:
        options.setdefault("discover_sibling_strings", True)
    if "source_namespace" not in options and "source_namespace" in expected_options:
        options["source_namespace"] = fingerprint.namespace.value
    if (
        descriptor.get("format_id") != format_id.value
        or source.get("fingerprint") != fingerprint.sha256
        or namespace is not None
        and namespace != fingerprint.namespace.value
        or options != expected_options
        or saved_fingerprints
        and not any(item == fingerprint for item in saved_fingerprints)
    ):
        raise ValueError("Saved source identity does not match its Project registration")
