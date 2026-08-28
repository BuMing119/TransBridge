from __future__ import annotations

from types import SimpleNamespace

from transbridge.ai_translator.project_terminology_runtime import resolve_project_terminology


def test_resolve_project_terminology_uses_injected_project_variant_factory() -> None:
    adapter = object()
    calls: list[tuple[str, str]] = []

    class Factory:
        def effective_adapter(self, project_id: str, variant_id: str):
            calls.append((project_id, variant_id))
            return adapter

    binding = resolve_project_terminology(
        SimpleNamespace(
            active_version_identity=("project-1", "variant-2"),
            effective_terminology_factory=Factory(),
        )
    )

    assert calls == [("project-1", "variant-2")]
    assert binding.translator_kwargs() == {
        "effective_terminology": adapter,
        "terminology_context": binding.context,
    }
    assert binding.term_database_kwargs() == {
        "effective_loader": adapter,
        "terminology_context": binding.context,
    }
    assert binding.context is not None
    assert (binding.context.local_project_id, binding.context.local_variant_id) == ("project-1", "variant-2")


def test_resolve_project_terminology_supports_callable_factory() -> None:
    adapter = object()
    binding = resolve_project_terminology(
        SimpleNamespace(
            active_version_identity=("project-1", "variant-1"),
            effective_terminology_factory=lambda _project_id, _variant_id: adapter,
        )
    )

    assert binding.adapter is adapter


def test_resolve_project_terminology_preserves_legacy_when_identity_or_storage_is_unavailable() -> None:
    missing_identity = resolve_project_terminology(
        SimpleNamespace(active_version_identity=None, effective_terminology_factory=lambda *_args: object())
    )
    unavailable = resolve_project_terminology(
        SimpleNamespace(
            active_version_identity=("project-1", "variant-1"),
            effective_terminology_factory=lambda *_args: (_ for _ in ()).throw(OSError("read-only store failed")),
        )
    )

    assert missing_identity.translator_kwargs() == {}
    assert missing_identity.term_database_kwargs() == {}
    assert unavailable.translator_kwargs() == {}
    assert unavailable.term_database_kwargs() == {}
