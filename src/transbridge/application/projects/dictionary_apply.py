# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Bind library matching to an active source and a revision-checked project command."""

from collections.abc import Iterable
from dataclasses import dataclass
import logging
from pathlib import Path
from types import EllipsisType
from typing import TYPE_CHECKING

from transbridge.application.contracts import OperationResult, RequestContext
from transbridge.application.io.identity import EntryKey
from transbridge.application.io.stage_policy import Stage
from transbridge.application.projects.dictionary_profile_binding import bind_dictionary_projection
from transbridge.application.projects.source_encoding import context_text_encodings
from transbridge.application.projects.source_terminology_scope import scoped_source_entry
from transbridge.config.dictionary_preferences import DictionaryPreferences, dictionary_preferences
from transbridge.config.text_encoding import TextEncodings
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.v2.ids import VariantRef
from transbridge.translation_memory.library import DictionaryLibrary
from transbridge.translation_memory.manager import ApplyResult

if TYPE_CHECKING:
    from transbridge.application.projects.gui_facade import GuiProjectCommandFacade

_LANGUAGE_CODES = {
    "english": "en",
    "chinese": "zh",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "spanish": "es",
    "portuguese": "pt",
    "danish": "da",
    "finnish": "fi",
    "norwegian": "no",
    "swedish": "sv",
    "polish": "pl",
    "czech": "cs",
    "hungarian": "hu",
    "russian": "ru",
    "greek": "el",
    "turkish": "tr",
    "arabic": "ar",
    "japanese": "ja",
    "korean": "ko",
    "thai": "th",
}


def _source_file(source) -> str:
    slot = getattr(source, "active_slot", None) or source
    for field in ("esp_path", "eet_path", "xt_path", "strings_path", "sst_path", "reference_path"):
        path = getattr(slot, field, None)
        if isinstance(path, (str, Path)) and path:
            return str(path)
    return ""


def dictionary_source_path(source) -> str:
    path = _source_file(source)
    slot = getattr(source, "active_slot", None) or source
    return path or getattr(slot, "label", "") or ""


def dictionary_mod_id(source) -> str:
    path = _source_file(source)
    slot = getattr(source, "active_slot", None) or source
    # Restored slots without a reference path already carry the source stem.
    return Path(path).stem if path else getattr(slot, "label", "") or ""


def dictionary_library(
    source,
    base_dir=None,
    *,
    encodings: TextEncodings | None = None,
    variant_identity: tuple[str, str] | None | EllipsisType = ...,
) -> DictionaryLibrary:
    """Use the Project text settings shared by desktop, source import and assistant commands."""
    encodings = encodings or context_text_encodings(source)
    game = getattr(source, "game_profile", None) or "skyrimse"
    if variant_identity is ...:
        variant_identity = getattr(source, "active_version_identity", None)
    if base_dir is None:
        base_dir = getattr(source, "dictionary_base_dir", None)
    return DictionaryLibrary(
        base_dir,
        game="skyrimse" if game == "skyrim_se" else game,
        source_locale=_LANGUAGE_CODES[encodings.source_language],
        target_locale=_LANGUAGE_CODES[encodings.target_language],
        projection_factory=bind_dictionary_projection(source, variant_identity),
    )


def automatic_dictionary_library(
    source,
    *,
    encodings: TextEncodings,
    variant_identity: tuple[str, str] | None | EllipsisType = ...,
    base_dir: Path | str | None = None,
    preferences: DictionaryPreferences | None = None,
) -> DictionaryLibrary | None:
    try:
        if (preferences if preferences is not None else dictionary_preferences(source)).apply_on_plugin_open:
            return dictionary_library(source, base_dir, encodings=encodings, variant_identity=variant_identity)
    except Exception:
        logging.getLogger(__name__).exception("无法读取自动套用词典设置或词典位置")
    return None


@dataclass
class DictionaryApplication:
    result: ApplyResult
    states: dict[EntryKey, tuple[str, int]]

    def commit(
        self,
        commands: "GuiProjectCommandFacade",
        runtime_context: RequestContext,
        *,
        expected_variant_ref: VariantRef,
        expected_project_revision: int,
        expected_variant_revision: int,
    ) -> OperationResult[dict]:
        return commands.replace_entry_states(
            self.states,
            runtime_context,
            expected_variant_ref=expected_variant_ref,
            expected_project_revision=expected_project_revision,
            expected_variant_revision=expected_variant_revision,
        )


def prepare_library_application(
    collection: Iterable[TranslationEntry],
    library: DictionaryLibrary,
    *,
    mod_file_id: str,
    plugin_scope: str | None = None,
) -> DictionaryApplication:
    """Prepare an exact patch without modifying source entries or their review stages."""
    matcher = library.matcher()
    projection = library.text_projection(to_default=False)
    result = ApplyResult()
    states = {}
    for entry in collection:
        if entry.translation or Stage.from_value(entry.stage) is not Stage.UNTRANSLATED:
            continue
        translation, via = matcher.lookup(entry, mod_file_id)
        if translation and projection is not None:
            target = entry if plugin_scope is None else scoped_source_entry(entry, plugin_scope)
            translation = projection.translate(target, translation)
        if not translation:
            result.misses += 1
            continue
        states[entry.identity] = (translation, entry.stage)
        result.applied += 1
        if via == "key":
            result.key_hits += 1
        else:
            result.text_hits += 1
    return DictionaryApplication(result, states)
