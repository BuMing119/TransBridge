# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Overlay one immutable localization profile on a base terminology snapshot."""

from __future__ import annotations

import base64
import binascii
from dataclasses import replace
import hashlib
import json
from typing import Protocol

from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
    EffectiveTerminologySnapshotPort,
)
from transbridge.application.terminology.identity import normalize_original, term_id
from transbridge.application.terminology.models import DecisionStatus, ScopeKind, TermDecision, TermScope
from transbridge.application.terminology.query_control import QueryCancellation

from .models import PublishedTerminologyProfile, logical_term_key

_VERSION_PREFIXES = {1: "profiled:v1:", 2: "profiled:v2:"}


class TerminologyProfileRevisionSource(Protocol):
    def selected_revision(self, project_id: str, variant_id: str) -> PublishedTerminologyProfile | None: ...

    def published_revision(self, profile_id: str, revision: int) -> PublishedTerminologyProfile | None: ...


class ProfiledEffectiveTerminologySnapshotPort:
    """Preserve legacy version IDs while making new profile snapshots exact."""

    def __init__(
        self,
        base: EffectiveTerminologySnapshotPort,
        profiles: TerminologyProfileRevisionSource,
    ) -> None:
        self._base = base
        self._profiles = profiles

    def snapshot(
        self,
        local_project_id: str,
        local_variant_id: str,
        version_id: str | None = None,
        *,
        cancellation: QueryCancellation | None = None,
    ) -> EffectiveTerminologySnapshot:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if version_id is not None and not is_profiled_version_id(version_id):
            # Old checkpoint/version references always retain the pre-profile
            # semantics even if a profile is selected today.
            return self._base.snapshot(local_project_id, local_variant_id, version_id)

        format_version = 2 if version_id is None else _format_version(version_id)
        if version_id is None:
            base = self._base.snapshot(local_project_id, local_variant_id)
            profile = self._profiles.selected_revision(local_project_id, local_variant_id)
            if profile is None:
                return base
        else:
            descriptor = decode_profiled_version_id(version_id)
            base_version_id = descriptor["base_version_id"]
            profile = self._profiles.published_revision(
                descriptor["profile_id"],
                int(descriptor["profile_revision"]),
            )
            if profile is None or profile.project_id != local_project_id:
                return EffectiveTerminologySnapshot(
                    local_project_id,
                    local_variant_id,
                    EffectiveSnapshotStatus.UNAVAILABLE,
                    diagnostics=("the exact terminology localization profile revision is unavailable",),
                )
            # A captured absence is exact evidence too. Never resolve it as
            # "latest" after a project base has subsequently been published.
            base = (
                EffectiveTerminologySnapshot(
                    local_project_id, local_variant_id, EffectiveSnapshotStatus.NO_PROJECT_VERSION
                )
                if base_version_id is None
                else self._base.snapshot(local_project_id, local_variant_id, base_version_id)
            )
            if profile.content_digest != descriptor["profile_digest"]:
                return EffectiveTerminologySnapshot(
                    local_project_id,
                    local_variant_id,
                    EffectiveSnapshotStatus.CORRUPT,
                    diagnostics=("terminology localization profile digest mismatch",),
                )

        if (
            format_version == 1
            and base.status is EffectiveSnapshotStatus.NO_PROJECT_VERSION
            and not any(item.scheme_owned for item in profile.content.mappings)
        ):
            return base
        if base.status not in {EffectiveSnapshotStatus.READY, EffectiveSnapshotStatus.NO_PROJECT_VERSION}:
            return base
        return _profiled_snapshot(base, profile, cancellation, format_version=format_version)


def is_profiled_version_id(value: str | None) -> bool:
    return bool(value and value.startswith(tuple(_VERSION_PREFIXES.values())))


def encode_profiled_version_id(
    base_version_id: str | None, profile: PublishedTerminologyProfile, *, format_version: int = 2
) -> str:
    payload = {
        "base_version_id": base_version_id,
        "profile_digest": profile.content_digest,
        "profile_id": profile.profile_id,
        "profile_revision": profile.revision,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _VERSION_PREFIXES[format_version] + base64.urlsafe_b64encode(encoded).rstrip(b"=").decode("ascii")


def _format_version(value: str) -> int:
    return next(version for version, prefix in _VERSION_PREFIXES.items() if value.startswith(prefix))


def decode_profiled_version_id(value: str) -> dict[str, str | int | None]:
    if not is_profiled_version_id(value):
        raise ValueError("not a profiled terminology version ID")
    encoded = value[len(_VERSION_PREFIXES[_format_version(value)]) :]
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        result = {
            "base_version_id": None if payload["base_version_id"] is None else str(payload["base_version_id"]),
            "profile_digest": str(payload["profile_digest"]),
            "profile_id": str(payload["profile_id"]),
            "profile_revision": int(payload["profile_revision"]),
        }
    except (binascii.Error, KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid profiled terminology version ID") from exc
    if result["base_version_id"] == "" or not result["profile_id"] or len(str(result["profile_digest"])) != 64:
        raise ValueError("invalid profiled terminology version descriptor")
    if int(result["profile_revision"]) < 0:
        raise ValueError("invalid profiled terminology profile revision")
    return result


def _profiled_snapshot(
    base: EffectiveTerminologySnapshot,
    profile: PublishedTerminologyProfile,
    cancellation: QueryCancellation | None = None,
    *,
    format_version: int = 2,
) -> EffectiveTerminologySnapshot:
    mappings = {}
    for item in profile.content.mappings:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        mappings[item.term_key] = item
    decisions = []
    missing = 0
    base_keys = set()
    for decision in base.decisions:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        key = logical_term_key(
            decision.original,
            scope_kind=decision.scope.kind.value,
            plugin_id=decision.scope.plugin_id,
        )
        base_keys.add(key)
        mapping = mappings.get(key)
        if mapping is not None:
            decisions.append(replace(decision, translation=mapping.translation))
        elif decision.is_effective:
            # Keep a shadow decision so lower-scope and legacy terminology do
            # not leak into an intentionally incomplete profile.
            decisions.append(replace(decision, translation="", status=DecisionStatus.UNRESOLVED, suppressed=True))
            missing += 1
        else:
            decisions.append(decision)
    for key, mapping in mappings.items():
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        # The saved Variant association is authority for these spellings.
        # A newly saved independent Variant need not own a base version yet.
        # Existing inactive/suppressed decisions still retain their state.
        if key in base_keys or (format_version == 1 and not mapping.scheme_owned):
            continue
        scope = TermScope(ScopeKind(mapping.scope_kind), mapping.plugin_id)
        decisions.append(
            TermDecision(
                term_id(
                    project_id=base.local_project_id,
                    variant_id=base.local_variant_id,
                    original=mapping.original,
                    scope=scope,
                ),
                base.local_project_id,
                base.local_variant_id,
                mapping.original,
                normalize_original(mapping.original),
                mapping.translation,
                scope,
                DecisionStatus.ADOPTED,
            )
        )
    version_id = encode_profiled_version_id(base.version_id, profile, format_version=format_version)
    payload = json.dumps(
        {
            "base_content_digest": base.content_digest,
            "base_version_id": base.version_id,
            "profile_content_digest": profile.content_digest,
            "profile_id": profile.profile_id,
            "profile_revision": profile.revision,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest_domain = f"transbridge.profiled-effective-terminology.v{format_version}\0".encode("ascii")
    digest = hashlib.sha256(digest_domain + payload).hexdigest()
    diagnostics = list(base.diagnostics)
    if missing:
        diagnostics.append(f"terminology localization profile has {missing} unmapped effective term(s)")
    return EffectiveTerminologySnapshot(
        base.local_project_id,
        base.local_variant_id,
        EffectiveSnapshotStatus.READY,
        version_id=version_id,
        content_digest=digest,
        decisions=tuple(decisions),
        diagnostics=tuple(diagnostics),
    )


__all__ = [
    "ProfiledEffectiveTerminologySnapshotPort",
    "TerminologyProfileRevisionSource",
    "decode_profiled_version_id",
    "encode_profiled_version_id",
    "is_profiled_version_id",
]
