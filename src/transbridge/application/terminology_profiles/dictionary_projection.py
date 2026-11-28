# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Project dictionary sentences between project default and a saved terminology scheme."""

from dataclasses import replace
import logging

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.converter.translation_entry import TranslationEntry

from .entry_projection import entry_plugin_id
from .models import PublishedTerminologyProfile, TerminologyProfileContent
from .projection import PreparedProfileProjector, TerminologyProfileProjector
from .transition_projection import destination_term_keys, transition_content

_logger = logging.getLogger(__name__)


class DictionaryTextProjection:
    """Use the actual scheme revision and default terminology frozen for one operation."""

    def __init__(
        self,
        profile: PublishedTerminologyProfile | None,
        base_snapshot: EffectiveTerminologySnapshot | None,
        *,
        to_default: bool,
    ) -> None:
        self._profile = profile
        self._to_default = to_default
        self._projector: PreparedProfileProjector | None = None
        self.checkpoint_identity: tuple[str | int | None, ...] = ("default",)
        if profile is None:
            return
        self.checkpoint_identity = (
            "scheme",
            profile.project_id,
            profile.profile_id,
            profile.revision,
            profile.content_digest,
            None if base_snapshot is None else base_snapshot.local_project_id,
            None if base_snapshot is None else base_snapshot.local_variant_id,
            None if base_snapshot is None else base_snapshot.status.value,
            None if base_snapshot is None else base_snapshot.version_id,
            None if base_snapshot is None else base_snapshot.content_digest,
        )
        if (
            base_snapshot is None
            or base_snapshot.local_project_id != profile.project_id
            or base_snapshot.status not in {EffectiveSnapshotStatus.READY, EffectiveSnapshotStatus.NO_PROJECT_VERSION}
        ):
            return
        self._projector = _prepare_transition(profile, base_snapshot, to_default=to_default)

    def translate(self, entry: TranslationEntry, text: str) -> str | None:
        """Return a complete projected sentence, or signal that the entry must be skipped."""
        if self._profile is None or not text:
            return text
        if self._projector is None:
            reasons = ("base_terminology_snapshot_unavailable",)
        else:
            result = self._projector.project(
                entry_key=entry.identity.serialize(),
                original=entry.original,
                common_translation=text,
                plugin_id=entry_plugin_id(entry),
            )
            if not result.diagnostics:
                return result.translation
            reasons = tuple(dict.fromkeys(item.code for item in result.diagnostics))
        _logger.warning(
            "跳过词典条目 %s 的术语转换（%s，方案 %s r%s）：%s",
            entry.identity.local_key,
            "入库" if self._to_default else "套用",
            self._profile.profile_id,
            self._profile.revision,
            ", ".join(reasons),
        )
        return None


def _prepare_transition(
    profile: PublishedTerminologyProfile,
    base_snapshot: EffectiveTerminologySnapshot,
    *,
    to_default: bool,
) -> PreparedProfileProjector:
    base_keys = destination_term_keys(profile, None, base_snapshot)
    profile_keys = destination_term_keys(None, profile, None)
    content = transition_content(profile, None, base_snapshot)
    if not to_default:
        content = TerminologyProfileContent(
            mappings=tuple(
                replace(mapping, translation=mapping.base_translation, base_translation=mapping.translation)
                for mapping in content.mappings
            ),
            bindings=profile.content.bindings,
        )
    return TerminologyProfileProjector().prepare(
        content,
        target_term_keys=base_keys if to_default else profile_keys,
        unavailable_term_keys=base_keys ^ profile_keys,
    )
