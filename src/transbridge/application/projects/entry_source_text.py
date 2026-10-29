"""Project text read models that use only original text retained in saved identities."""

from transbridge.application.contracts import DomainError
from transbridge.application.preflight.inputs import SOURCE_CONTEXT_KNOWN
from transbridge.converter.translation_entry import TranslationEntry

from .source_content import source_content_identity


def registered_source_namespace(source, raw_source, variant):
    identity = source_content_identity(raw_source)
    matches = tuple(
        item.namespace
        for item in variant.source_fingerprints
        if item.sha256 == source.fingerprint and (identity is None or identity == item.namespace.value)
    )
    return matches[0] if len(matches) == 1 else None


def namespace_source_id(namespace):
    """A read-model identity for a saved group without a source registration."""
    return f"namespace:{namespace.value}"


def known_original_entries(states, *, include_external_refs=False):
    states = tuple(states)
    if any(state.entry_key.original is None for state in states):
        return None
    return tuple(
        TranslationEntry(
            id=state.entry_key.local_key,
            key=state.entry_key.local_key,
            original=state.entry_key.original,
            translation=state.translation,
            stage=state.stage.value,
            context=None,
            entry_key=state.entry_key,
            revision=state.revision,
            provenance=state.provenance,
            external_refs=state.external_refs if include_external_refs else (),
            metadata=((SOURCE_CONTEXT_KNOWN, False),),
        )
        for state in states
    )


def available_source_baselines(baselines, project, variant, context):
    try:
        return baselines.provide(project, variant.ref, context)
    except DomainError as exc:
        if exc.code != "LEGACY_SOURCE_BASELINE_REQUIRED":
            raise
        return ()
