"""Read immutable naming-scheme inputs from installed source content."""

from __future__ import annotations

from transbridge.application.contracts import DomainError
from transbridge.application.terminology_profiles.entry_projection import ProfileEntryInput, entry_plugin_id
from transbridge.converter.translation_entry import TranslationEntry

from .entry_source_text import registered_source_namespace
from .file_source_catalog import registered_file_sources
from .installed_source_request import InstalledSourceRequest
from .source_hydration_cache import SourceHydrationCache


class ProfileEntryInputReader:
    def __init__(self, preparer):
        self._preparer = preparer
        self._hydrations = SourceHydrationCache()

    def read(self, project, snapshot, context):
        states = {state.entry_key: state for state in snapshot.entries if not state.tombstone}
        output = {
            key: ProfileEntryInput(key.serialize(), key.original, state.translation, None)
            for key, state in states.items()
            if key.original is not None
        }
        for source, raw in registered_file_sources(project):
            namespace = registered_source_namespace(source, raw, snapshot)
            if namespace is None or source.source_artifact_id is None:
                continue
            options = dict(source.format_options)
            options["source_namespace"] = namespace.value
            try:
                prepared = self._hydrations.read_installed(
                    self._preparer,
                    InstalledSourceRequest(
                        source.location, source.format_id, source.fingerprint, tuple(options.items())
                    ),
                    context,
                    scope_key=(project.envelope.identity, snapshot.ref.identity.value),
                    source=raw,
                )
            except (DomainError, OSError) as exc:
                # A damaged installed source cannot silently discard plugin scope.
                raise RuntimeError(f"无法读取方案转换所需的来源信息：{source.display_name or source.location}") from exc
            if prepared is None or prepared.fingerprint not in snapshot.source_fingerprints:
                raise RuntimeError(f"工程来源与当前版本不一致：{source.display_name or source.location}")
            for parsed in prepared.entries:
                if (state := states.get(parsed.entry_key)) is None:
                    continue
                entry = TranslationEntry(
                    id=parsed.legacy_id,
                    key=parsed.entry_key.local_key,
                    original=parsed.original,
                    translation=state.translation,
                    stage=state.stage.value,
                    context=parsed.context,
                    entry_key=parsed.entry_key,
                    metadata=parsed.metadata,
                    string_id=parsed.string_id,
                )
                output[parsed.entry_key] = ProfileEntryInput(
                    parsed.entry_key.serialize(),
                    parsed.original,
                    state.translation,
                    entry_plugin_id(entry),
                )
        return output
