"""Validate plugin text at source-bound physical positions, before conflict filtering."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path

from transbridge.application.io.contracts import WriteRequest
from transbridge.application.io.plugin_bsa_output import bsa_output_enabled
from transbridge.application.io.plugin_localized_sources import localized_entry_identity, localized_id_variants
from transbridge.application.io.plugin_publication_source import prepared_plugin_source
from transbridge.application.io.plugin_write import _snapshot_localized_sources
from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY, StageOperation
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from transbridge.parser.strings_file import PluginStringsLookup
from transbridge.writer.plugin_source import capture_plugin_write_source

from .models import ValidationReport


def validate_plugin_fidelity(request: WriteRequest, staging_path: str) -> ValidationReport:
    """Compare every physical string, including unrequested and conflict-skipped ones.

    A source-qualified EntryKey contains the original text. After publication that
    text is the translation, so reparsed public entry identities cannot serve as
    round-trip locators. Use the immutable source's physical order instead; never
    sort translated strings or remove duplicate strings before comparison.
    """
    try:
        source = _source_entries(request)
        expected_texts = _expected_texts(request, source)
    except (OSError, TypeError, ValueError) as exc:
        return _invalid(request, "FIDELITY_SUMMARY_FAILED", exc)

    try:
        target = Path(staging_path)
        plugin = SSEPluginWithContext.from_file(target)
        lookup = None
        if _snapshot_localized_sources(request.source_snapshot):
            language = str(
                dict(request.options).get(
                    "language", dict(request.source_snapshot.metadata).get("localized_language", "english")
                )
            )
            lookup = (
                PluginStringsLookup.from_plugin(target, language)
                if bsa_output_enabled(request.source_snapshot, request.options)
                else PluginStringsLookup.from_strings_dir(target.parent / "Strings", target.stem, language)
            )
            if lookup is None:
                raise ValueError("staged localized strings are unavailable")
        actual = tuple(entry for entry, _subrecord in capture_plugin_write_source(plugin, lookup))
    except (OSError, TypeError, ValueError) as exc:
        return _invalid(request, "REPARSE_FAILED", exc)

    expected = _physical_summary(source, expected_texts)
    observed = _physical_summary(actual, tuple(entry.original for entry in actual))
    valid = expected == observed
    digest = hashlib.sha256(json.dumps(observed, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return ValidationReport(
        valid,
        request.format_id,
        True,
        True,
        valid,
        len(actual),
        digest,
        "VALID" if valid else "FIDELITY_MISMATCH",
        "plugin physical string fidelity passed"
        if valid
        else "staged plugin strings differ from their source-bound write positions",
    )


def _source_entries(request: WriteRequest) -> tuple[TranslationEntry, ...]:
    snapshot = request.source_snapshot
    if snapshot is None or snapshot.content is None:
        raise ValueError("plugin fidelity requires captured source bytes")
    if hashlib.sha256(snapshot.content).hexdigest() != snapshot.sha256:
        raise ValueError("plugin source snapshot fingerprint differs")
    return prepared_plugin_source(snapshot, dict(request.options)).physical_entries


def _expected_texts(request: WriteRequest, source: tuple[TranslationEntry, ...]) -> tuple[str, ...]:
    by_key: dict[str, list[int]] = defaultdict(list)
    for position, entry in enumerate(source):
        by_key[entry.key].append(position)
    expected = [entry.original for entry in source]
    variants = localized_id_variants(_snapshot_localized_sources(request.source_snapshot))
    localized: dict[tuple, str] = {}
    used: set[int] = set()
    policy = request.stage_policy or DEFAULT_STAGE_POLICY
    namespace = dict(request.source_snapshot.metadata).get("source_namespace")
    for entry in request.entries:
        if not isinstance(entry, TranslationEntry) or entry.identity.namespace.value != namespace:
            raise ValueError("plugin write entry has no matching source identity")
        positions = by_key.get(entry.key, ())
        if len(positions) > 1 and not entry.requires_original_match:
            raise ValueError("ambiguous plugin write requires its source original")
        matches = [position for position in positions if source[position].original == entry.original]
        if len(matches) != 1 or matches[0] in used:
            raise ValueError("plugin write entry has no unique physical source position")
        position = matches[0]
        used.add(position)
        if source[position].string_id != entry.string_id:
            raise ValueError("plugin write changed its localized string ID")
        decision = policy.evaluate(entry.stage, entry.translation, StageOperation.PUBLISH, original=entry.original)
        if decision.publish_text is None:
            raise ValueError("blocking stage reached plugin fidelity validation")
        expected[position] = decision.publish_text
        if entry.string_id is not None:
            identity = localized_entry_identity(source[position], variants)
            if identity in localized and localized[identity] != decision.publish_text:
                raise ValueError("plugin writes disagree for a shared localized string ID")
            localized[identity] = decision.publish_text
    # Multiple physical references to one localized ID necessarily share text.
    return tuple(
        localized.get(localized_entry_identity(entry, variants), expected[position])
        if entry.string_id is not None
        else expected[position]
        for position, entry in enumerate(source)
    )


def _physical_summary(entries: tuple[TranslationEntry, ...], texts: tuple[str, ...]) -> tuple[tuple, ...]:
    return tuple(
        (position, entry.key, entry.dsd_type, entry.dsd_index, entry.string_id, text)
        for position, (entry, text) in enumerate(zip(entries, texts, strict=True))
    )


def _invalid(request: WriteRequest, code: str, exc: Exception) -> ValidationReport:
    return ValidationReport(
        False,
        request.format_id,
        True,
        False,
        False,
        0,
        code=code,
        message=f"plugin fidelity validation failed ({type(exc).__name__}): {exc}",
    )
