# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Bind game text reads to source snapshots."""

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity
from transbridge.config.text_encoding import (
    DEFAULT_TEXT_ENCODING,
    LEGACY_READ_POLICY,
    STRICT_READ_POLICY,
    XTRANSLATOR_READ_POLICY,
    TextReadOptions,
    language_from_suffix,
)


def request_text_read_options(options) -> TextReadOptions:
    options = dict(options)
    return TextReadOptions(
        options.get("input_encoding", DEFAULT_TEXT_ENCODING),
        options.get("source_language", language_from_suffix(options.get("language"))),
        options.get("text_read_policy", XTRANSLATOR_READ_POLICY),
    )


def snapshot_read_options(snapshot, format_options=()) -> dict[str, str]:
    """Return the read facts previously associated with the source bytes."""
    metadata = dict(snapshot.metadata)
    saved = dict(format_options)
    policy = metadata.get("text_read_policy", saved.get("text_read_policy"))
    encoding = snapshot.encoding or saved.get("input_encoding")
    localized_language = metadata.get("localized_language", saved.get("language"))
    return TextReadOptions(
        encoding or DEFAULT_TEXT_ENCODING,
        metadata.get(
            "source_language",
            saved.get("source_language", language_from_suffix(localized_language)),
        ),
        policy if policy is not None else (STRICT_READ_POLICY if encoding else LEGACY_READ_POLICY),
    ).as_options()


def source_text_metadata(options: TextReadOptions) -> tuple[tuple[str, str], ...]:
    return (("source_language", options.source_language), ("text_read_policy", options.text_read_policy))


def source_text_fallback_diagnostics(count: int) -> tuple[Diagnostic, ...]:
    if not count:
        return ()
    return (
        Diagnostic(
            "GAME_TEXT_FALLBACK_USED",
            f"{count} game text field(s) used a fallback encoding.",
            DiagnosticSeverity.INFO,
            details=(("count", count),),
        ),
    )
