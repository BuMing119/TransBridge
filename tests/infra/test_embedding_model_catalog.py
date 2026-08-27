from __future__ import annotations

from pathlib import Path

import pytest

from transbridge.infra import embedding_model_catalog as catalog_module
from transbridge.infra.embedding_model_catalog import (
    EmbeddingModelCatalogError,
    load_embedding_model_catalog,
    user_embedding_model_catalog_path,
)
from transbridge.infra.embedding_model_store import EmbeddingModelStore

DEFAULT_CATALOG = Path(__file__).parents[2] / "src" / "transbridge" / "resources" / "embedding_models.toml"


def _model_table(
    model_id: str,
    *,
    title: str,
    repo_id: str,
    revision: str,
    recommended: str,
    dimension: str = "384",
) -> str:
    return f'''[[models]]
id = "{model_id}"
title = "{title}"
repo_id = "{repo_id}"
revision = "{revision}"
description = "Test model"
dimension = {dimension}
download_size_mb = 100
recommended = {recommended}
'''


def _catalog(*models: str) -> str:
    return 'document_type = "transbridge.embedding-model-catalog"\nschema_version = 1\n\n' + "\n".join(models)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_bundled_catalog_contains_current_models_in_display_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(catalog_module, "get_data_dir", lambda: str(tmp_path))

    presets = load_embedding_model_catalog()

    assert [preset.id for preset in presets] == ["multilingual-minilm-l12-v2", "multilingual-mpnet-base-v2"]
    assert presets[0].repo_id == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert presets[0].revision == "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
    assert [preset.recommended for preset in presets] == [True, False]


def test_user_override_is_authoritative_and_preserves_toml_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(catalog_module, "get_data_dir", lambda: str(tmp_path))
    override = user_embedding_model_catalog_path()
    _write(
        override,
        _catalog(
            _model_table(
                "quality-model",
                title="Quality",
                repo_id="example/quality-model",
                revision="a" * 40,
                recommended="true",
            ),
            _model_table(
                "small-model",
                title="Small",
                repo_id="example/small-model",
                revision="b" * 40,
                recommended="false",
            ),
        ),
    )

    store = EmbeddingModelStore(tmp_path / "models")

    assert [state.preset.id for state in store.list_models()] == ["quality-model", "small-model"]


@pytest.mark.parametrize(
    ("document", "message"),
    (
        ("schema_version = 1\nmodels = []\n", "fields are invalid"),
        (
            _catalog(
                _model_table(
                    "../escape",
                    title="Unsafe",
                    repo_id="example/unsafe",
                    revision="a" * 40,
                    recommended="true",
                )
            ),
            "safe lowercase",
        ),
        (
            _catalog(
                _model_table(
                    "floating-revision",
                    title="Floating",
                    repo_id="example/floating",
                    revision="main",
                    recommended="true",
                )
            ),
            "fixed 40-character",
        ),
        (
            _catalog(
                _model_table(
                    "wrong-dimension",
                    title="Wrong",
                    repo_id="example/wrong",
                    revision="a" * 40,
                    recommended="true",
                    dimension="true",
                )
            ),
            "positive integer",
        ),
        (
            _catalog(
                _model_table(
                    "first-model",
                    title="First",
                    repo_id="example/first",
                    revision="a" * 40,
                    recommended="false",
                ),
                _model_table(
                    "second-model",
                    title="Second",
                    repo_id="example/second",
                    revision="b" * 40,
                    recommended="false",
                ),
            ),
            "exactly one recommended",
        ),
    ),
)
def test_invalid_catalog_fails_closed_before_storage_or_download(
    tmp_path: Path,
    document: str,
    message: str,
) -> None:
    path = _write(tmp_path / "catalog.toml", document)
    model_root = tmp_path / "models"
    downloads: list[object] = []

    with pytest.raises(EmbeddingModelCatalogError, match=message):
        EmbeddingModelStore(model_root, downloader=lambda **kwargs: downloads.append(kwargs), catalog_path=path)

    assert not model_root.exists()
    assert downloads == []


def test_duplicate_model_ids_reject_the_entire_catalog(tmp_path: Path) -> None:
    model = _model_table(
        "same-model",
        title="Same",
        repo_id="example/same",
        revision="a" * 40,
        recommended="true",
    )
    duplicate = model.replace("recommended = true", "recommended = false")
    path = _write(tmp_path / "duplicate.toml", _catalog(model, duplicate))

    with pytest.raises(EmbeddingModelCatalogError, match="duplicate model IDs"):
        load_embedding_model_catalog(path)


def test_missing_explicit_catalog_reports_its_path(tmp_path: Path) -> None:
    path = tmp_path / "missing.toml"

    with pytest.raises(EmbeddingModelCatalogError, match="missing.toml"):
        load_embedding_model_catalog(path)


def test_default_catalog_file_is_directly_loadable() -> None:
    assert len(load_embedding_model_catalog(DEFAULT_CATALOG)) == 2
