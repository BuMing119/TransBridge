"""Read source-owned input fields for only the asynchronous result targets."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from transbridge.application.translation.result_merge import source_locator_signature

from .entry_source_text import available_source_baselines
from .file_source_catalog import registered_file_sources
from .installed_source_request import InstalledSourceRequest
from .result_merge import _target_records
from .source_content import source_content_identity

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, eq=False, slots=True)
class _InstalledBaselineIdentity:
    baseline: object

    def __hash__(self):
        return id(self.baseline)

    def __eq__(self, other):
        return isinstance(other, _InstalledBaselineIdentity) and self.baseline is other.baseline


def read_result_source_texts(project, variant, keys, context, *, baselines, preparer, hydrations):
    """Read installed metadata when available, retaining originals carried by saved identities."""
    namespaces = {key.namespace for key in keys}
    fingerprints = {value.namespace: value for value in variant.source_fingerprints}
    available = available_source_baselines(baselines, project, variant, context)
    result = {
        entry.entry_key: (entry.entry_key.original, None, None)
        for entry in _target_records(variant.entries, keys).values()
        if not entry.tombstone and entry.entry_key.original is not None
    }
    for source, raw in registered_file_sources(project):
        if source.source_artifact_id is None:
            continue
        namespace = source_content_identity(raw)
        matches = tuple(
            baseline
            for baseline in available
            if baseline.fingerprint.sha256 == source.fingerprint
            and (namespace is None or baseline.fingerprint.namespace.value == namespace)
        )
        if len(matches) != 1 or matches[0].fingerprint.namespace not in namespaces:
            continue
        baseline = matches[0]
        fingerprint = baseline.fingerprint
        if fingerprints.get(fingerprint.namespace) != fingerprint:
            continue
        options = dict(source.format_options)
        options["source_namespace"] = fingerprint.namespace.value
        try:
            prepared = hydrations.read_installed(
                preparer,
                InstalledSourceRequest(source.location, source.format_id, source.fingerprint, tuple(options.items())),
                context,
                scope_key=(project.envelope.identity, variant.ref.identity.value, _InstalledBaselineIdentity(baseline)),
                source=raw,
            )
        except Exception:
            _logger.exception(
                "Result source metadata is unavailable; only saved identity text remains: %s", source.location
            )
            continue
        if prepared is None or prepared.fingerprint != fingerprint:
            continue
        for entry in prepared.entries:
            if entry.entry_key in keys:
                result[entry.entry_key] = (
                    entry.original,
                    entry.context or "",
                    source_locator_signature(entry.metadata, entry.string_id, prepared.fingerprint.sha256),
                )
    # Only saved EntryKey originals supplement missing sources. Context and
    # locators remain unknown, so existing result guards still protect them.
    return result
