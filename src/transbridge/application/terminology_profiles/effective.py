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
from transbridge.application.terminology.models import DecisionStatus

from .models import PublishedTerminologyProfile, logical_term_key

_VERSION_PREFIX = "profiled:v1:"


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
    ) -> EffectiveTerminologySnapshot:
        if version_id is not None and not is_profiled_version_id(version_id):
            # Old checkpoint/version references always retain the pre-profile
            # semantics even if a profile is selected today.
            return self._base.snapshot(local_project_id, local_variant_id, version_id)

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
            base = self._base.snapshot(local_project_id, local_variant_id, base_version_id)
            if profile.content_digest != descriptor["profile_digest"]:
                return EffectiveTerminologySnapshot(
                    local_project_id,
                    local_variant_id,
                    EffectiveSnapshotStatus.CORRUPT,
                    diagnostics=("terminology localization profile digest mismatch",),
                )

        if base.status is not EffectiveSnapshotStatus.READY:
            return base
        return _profiled_snapshot(base, profile)


def is_profiled_version_id(value: str | None) -> bool:
    return bool(value and value.startswith(_VERSION_PREFIX))


def encode_profiled_version_id(base_version_id: str, profile: PublishedTerminologyProfile) -> str:
    payload = {
        "base_version_id": base_version_id,
        "profile_digest": profile.content_digest,
        "profile_id": profile.profile_id,
        "profile_revision": profile.revision,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _VERSION_PREFIX + base64.urlsafe_b64encode(encoded).rstrip(b"=").decode("ascii")


def decode_profiled_version_id(value: str) -> dict[str, str | int]:
    if not is_profiled_version_id(value):
        raise ValueError("not a profiled terminology version ID")
    encoded = value[len(_VERSION_PREFIX) :]
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        result = {
            "base_version_id": str(payload["base_version_id"]),
            "profile_digest": str(payload["profile_digest"]),
            "profile_id": str(payload["profile_id"]),
            "profile_revision": int(payload["profile_revision"]),
        }
    except (binascii.Error, KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid profiled terminology version ID") from exc
    if not result["base_version_id"] or not result["profile_id"] or len(str(result["profile_digest"])) != 64:
        raise ValueError("invalid profiled terminology version descriptor")
    if int(result["profile_revision"]) < 0:
        raise ValueError("invalid profiled terminology profile revision")
    return result


def _profiled_snapshot(
    base: EffectiveTerminologySnapshot,
    profile: PublishedTerminologyProfile,
) -> EffectiveTerminologySnapshot:
    mappings = {item.term_key: item for item in profile.content.mappings}
    decisions = []
    missing = 0
    for decision in base.decisions:
        mapping = mappings.get(
            logical_term_key(
                decision.original,
                scope_kind=decision.scope.kind.value,
                plugin_id=decision.scope.plugin_id,
            )
        )
        if mapping is not None:
            decisions.append(replace(decision, translation=mapping.translation))
        elif decision.is_effective:
            # Keep a shadow decision so lower-scope and legacy terminology do
            # not leak into an intentionally incomplete profile.
            decisions.append(replace(decision, translation="", status=DecisionStatus.UNRESOLVED, suppressed=True))
            missing += 1
        else:
            decisions.append(decision)
    version_id = encode_profiled_version_id(base.version_id or "", profile)
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
    digest = hashlib.sha256(b"transbridge.profiled-effective-terminology.v1\0" + payload).hexdigest()
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
