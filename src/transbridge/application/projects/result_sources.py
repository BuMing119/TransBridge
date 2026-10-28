"""Read source-owned input fields for only the asynchronous result targets."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from transbridge.application.translation.result_merge import source_locator_signature

from .file_source_catalog import registered_file_sources
from .provisioning import ProjectSourceRequest
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
    """Return verified text; unreadable sources remain absent and become pending."""
    namespaces = {key.namespace for key in keys}
    fingerprints = {value.namespace: value for value in variant.source_fingerprints}
    available = baselines.provide(project, variant.ref, context)
    result = {}
    for source, raw in registered_file_sources(project):
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
                ProjectSourceRequest(source.location, source.format_id, source.fingerprint, tuple(options.items())),
                context,
                scope_key=(project.envelope.identity, variant.ref.identity.value, _InstalledBaselineIdentity(baseline)),
                source=raw,
            )
        except Exception:
            _logger.exception("Result source could not be verified; its entries remain pending: %s", source.location)
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
    # Missing registrations and changed identities deliberately have no fallback
    # to task-start text, which could otherwise authorize an obsolete result.
    return result
