"""Load and validate the editable local Embedding model catalog."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
import re
import tomllib

from transbridge.config.paths import get_data_dir

_DOCUMENT_TYPE = "transbridge.embedding-model-catalog"
_SCHEMA_VERSION = 1
_CATALOG_FILENAME = "embedding_models.toml"
_MAX_CATALOG_BYTES = 256 * 1024
_MAX_MODELS = 100
_DOCUMENT_KEYS = frozenset({"document_type", "schema_version", "models"})
_MODEL_KEYS = frozenset({
    "id",
    "title",
    "repo_id",
    "revision",
    "description",
    "dimension",
    "download_size_mb",
    "recommended",
})
_MODEL_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_REPO_PART_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_REVISION_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
_WINDOWS_RESERVED_NAMES = frozenset({
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
})


class EmbeddingModelCatalogError(ValueError):
    """Raised when an Embedding model catalog cannot be safely loaded."""

    def __init__(self, source: str, message: str) -> None:
        self.source = source
        super().__init__(f"Embedding model catalog {source}: {message}")


@dataclass(frozen=True, slots=True)
class EmbeddingModelPreset:
    """A local embedding model offered by the application."""

    id: str
    title: str
    repo_id: str
    revision: str
    description: str
    dimension: int
    download_size_mb: int
    recommended: bool = False


def user_embedding_model_catalog_path() -> Path:
    """Return the optional user override path for the model catalog."""

    return Path(get_data_dir()) / "config" / _CATALOG_FILENAME


def load_embedding_model_catalog(path: str | Path | None = None) -> tuple[EmbeddingModelPreset, ...]:
    """Load a complete catalog, preferring the user override when present.

    An existing override is authoritative: invalid override content fails
    closed instead of silently reverting to the bundled catalog.
    """

    if path is not None:
        source = Path(path)
        return _parse_catalog(_read_path(source), str(source))

    override = user_embedding_model_catalog_path()
    if override.exists():
        return _parse_catalog(_read_path(override), str(override))

    resource = files("transbridge.resources").joinpath(_CATALOG_FILENAME)
    source_label = f"transbridge.resources/{_CATALOG_FILENAME}"
    try:
        raw = resource.read_bytes()
    except OSError as exc:
        raise EmbeddingModelCatalogError(source_label, f"cannot read bundled catalog: {exc}") from exc
    return _parse_catalog(raw, source_label)


def _read_path(path: Path) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EmbeddingModelCatalogError(str(path), f"cannot read catalog: {exc}") from exc
    return raw


def _parse_catalog(raw: bytes, source: str) -> tuple[EmbeddingModelPreset, ...]:
    if len(raw) > _MAX_CATALOG_BYTES:
        raise EmbeddingModelCatalogError(source, "document exceeds the 256 KiB size limit")
    try:
        payload = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise EmbeddingModelCatalogError(source, f"invalid UTF-8 TOML: {exc}") from exc
    if not isinstance(payload, dict):
        raise EmbeddingModelCatalogError(source, "document root must be a table")
    _require_exact_keys(payload, _DOCUMENT_KEYS, "document", source)
    if payload["document_type"] != _DOCUMENT_TYPE:
        raise EmbeddingModelCatalogError(source, f"document_type must be {_DOCUMENT_TYPE!r}")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != _SCHEMA_VERSION:
        raise EmbeddingModelCatalogError(source, f"unsupported schema_version: {payload['schema_version']!r}")

    raw_models = payload["models"]
    if not isinstance(raw_models, list) or not raw_models:
        raise EmbeddingModelCatalogError(source, "document.models must be a non-empty array of tables")
    if len(raw_models) > _MAX_MODELS:
        raise EmbeddingModelCatalogError(source, f"document.models exceeds the {_MAX_MODELS} model limit")

    presets = tuple(_parse_model(value, index, source) for index, value in enumerate(raw_models))
    ids = [preset.id for preset in presets]
    if len(ids) != len(set(ids)):
        raise EmbeddingModelCatalogError(source, "document.models contains duplicate model IDs")
    recommended_count = sum(preset.recommended for preset in presets)
    if recommended_count != 1:
        raise EmbeddingModelCatalogError(source, "document.models must contain exactly one recommended model")
    return presets


def _parse_model(value: object, index: int, source: str) -> EmbeddingModelPreset:
    location = f"document.models[{index}]"
    if not isinstance(value, dict):
        raise EmbeddingModelCatalogError(source, f"{location} must be a table")
    _require_exact_keys(value, _MODEL_KEYS, location, source)

    model_id = _require_text(value["id"], f"{location}.id", source, maximum=64)
    if _MODEL_ID_PATTERN.fullmatch(model_id) is None or model_id in _WINDOWS_RESERVED_NAMES:
        raise EmbeddingModelCatalogError(source, f"{location}.id must be a safe lowercase hyphenated identifier")
    repo_id = _require_text(value["repo_id"], f"{location}.repo_id", source, maximum=200)
    repo_parts = repo_id.split("/")
    if len(repo_parts) != 2 or any(_REPO_PART_PATTERN.fullmatch(part) is None for part in repo_parts):
        raise EmbeddingModelCatalogError(source, f"{location}.repo_id must use a safe namespace/name value")
    revision = _require_text(value["revision"], f"{location}.revision", source, maximum=40)
    if _REVISION_PATTERN.fullmatch(revision) is None:
        raise EmbeddingModelCatalogError(
            source, f"{location}.revision must be a fixed 40-character lowercase commit SHA"
        )

    recommended = value["recommended"]
    if type(recommended) is not bool:
        raise EmbeddingModelCatalogError(source, f"{location}.recommended must be a boolean")
    return EmbeddingModelPreset(
        id=model_id,
        title=_require_text(value["title"], f"{location}.title", source, maximum=120),
        repo_id=repo_id,
        revision=revision,
        description=_require_text(value["description"], f"{location}.description", source, maximum=500),
        dimension=_require_positive_int(value["dimension"], f"{location}.dimension", source),
        download_size_mb=_require_positive_int(value["download_size_mb"], f"{location}.download_size_mb", source),
        recommended=recommended,
    )


def _require_exact_keys(value: dict[str, object], expected: frozenset[str], location: str, source: str) -> None:
    actual = frozenset(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details = []
    if missing:
        details.append(f"missing {missing}")
    if unknown:
        details.append(f"unknown {unknown}")
    raise EmbeddingModelCatalogError(source, f"{location} fields are invalid: {', '.join(details)}")


def _require_text(value: object, location: str, source: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise EmbeddingModelCatalogError(
            source, f"{location} must be trimmed non-empty text up to {maximum} characters"
        )
    return value


def _require_positive_int(value: object, location: str, source: str) -> int:
    if type(value) is not int or value <= 0:
        raise EmbeddingModelCatalogError(source, f"{location} must be a positive integer")
    return value


__all__ = [
    "EmbeddingModelCatalogError",
    "EmbeddingModelPreset",
    "load_embedding_model_catalog",
    "user_embedding_model_catalog_path",
]
