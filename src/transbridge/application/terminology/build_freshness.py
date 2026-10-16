"""Persistable build-input proof independent of later draft edits."""

from __future__ import annotations

from .errors import StaleBuildError
from .identity import canonical_digest
from .input_capture import BuildInputSnapshot
from .models import BuildResult


def build_input_digest(snapshot: BuildInputSnapshot) -> str:
    """Identify the source/configuration state, excluding mutable review state."""

    return canonical_digest(
        {
            "project_id": snapshot.project_id,
            "project_revision": snapshot.project_revision,
            "variant_id": snapshot.variant_id,
            "variant_revision": snapshot.variant_revision,
            "variant_content_digest": snapshot.variant_content_digest,
            "sources": tuple((source.registration, source.canonical_payload()) for source in snapshot.sources),
            "relations": snapshot.relations,
            "config_digest": snapshot.config_digest,
        },
        namespace="terminology.build-input-proof.v1",
    )


def require_current_build(build: BuildResult, expected_input_digest: str) -> BuildResult:
    build.require_publishable()
    if build.input_digest is None:
        raise StaleBuildError("无法确认此术语构建的来源状态，请重新构建术语库后发布。")
    if build.input_digest != expected_input_digest:
        raise StaleBuildError("工程、翻译版本、来源或构建配置已经变化，请重新构建术语库后发布。")
    return build


__all__ = ["build_input_digest", "require_current_build"]
