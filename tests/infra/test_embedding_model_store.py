from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from transbridge.infra import embedding_model_store
from transbridge.infra.embedding_model_catalog import EmbeddingModelPreset, load_embedding_model_catalog
from transbridge.infra.embedding_model_store import (
    EmbeddingModelDownloadCancelled,
    EmbeddingModelStore,
)

MINILM_ID = "multilingual-minilm-l12-v2"
DEFAULT_CATALOG = Path(__file__).parents[2] / "src" / "transbridge" / "resources" / "embedding_models.toml"


def _store(root: Path | None = None, downloader=None) -> EmbeddingModelStore:
    return EmbeddingModelStore(root, downloader=downloader, catalog_path=DEFAULT_CATALOG)


def _preset(model_id: str = MINILM_ID) -> EmbeddingModelPreset:
    return next(item for item in load_embedding_model_catalog(DEFAULT_CATALOG) if item.id == model_id)


def _create_installed_model(root: Path, model_id: str = MINILM_ID) -> Path:
    preset = _preset(model_id)
    target = root / model_id
    target.mkdir(parents=True)
    (target / "modules.json").write_text('[{"path": ""}]', encoding="utf-8")
    (target / "config.json").write_text("{}", encoding="utf-8")
    (target / "model.safetensors").write_bytes(b"weights")
    (target / "transbridge-model.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "installed",
                "model_id": preset.id,
                "repo_id": preset.repo_id,
                "revision": preset.revision,
                "dimension": preset.dimension,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return target


def test_default_root_and_preset_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(embedding_model_store, "get_data_dir", lambda: str(tmp_path))

    store = EmbeddingModelStore(catalog_path=DEFAULT_CATALOG)
    states = store.list_models()

    assert store.root == tmp_path / "models" / "embedding"
    assert [state.preset.repo_id for state in states] == [
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
    ]
    assert [(state.preset.dimension, state.preset.download_size_mb) for state in states] == [(384, 500), (768, 1200)]
    assert states[0].preset.recommended is True
    assert states[1].preset.recommended is False
    assert all(not state.installed for state in states)


def test_download_installs_valid_snapshot_and_filters_redundant_variants(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def downloader(**kwargs: object) -> str:
        calls.append(kwargs)
        destination = Path(str(kwargs["local_dir"]))
        (destination / "modules.json").write_text('[{"path": ""}]', encoding="utf-8")
        (destination / "config.json").write_text("{}", encoding="utf-8")
        (destination / "model.safetensors").write_bytes(b"weights")
        return str(destination)

    progress: list[tuple[int, int, str]] = []
    store = _store(tmp_path / "models", downloader)

    installed = store.download(
        MINILM_ID, progress=lambda current, total, message: progress.append((current, total, message))
    )

    assert installed == store.root / MINILM_ID
    assert store.installed_path(MINILM_ID) == installed
    assert (installed / "modules.json").is_file()
    assert (installed / "model.safetensors").is_file()
    assert progress[0] == (0, 0, "正在连接 Hugging Face 并获取模型文件信息…")
    assert progress[-2:] == [(0, 0, "模型文件下载完成，正在校验并安装…"), (1, 1, "模型安装完成。")]
    assert calls[0]["repo_id"] == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert calls[0]["revision"] == _preset().revision
    ignored = calls[0]["ignore_patterns"]
    assert isinstance(ignored, tuple)
    assert "pytorch_model.bin" in ignored
    assert "*.onnx" in ignored
    assert not any("safetensors" in pattern for pattern in ignored)
    assert not list(store.root.glob("*.download"))
    assert store.model_identity(MINILM_ID) == {
        "schema_version": 1,
        "status": "installed",
        "model_id": MINILM_ID,
        "repo_id": _preset().repo_id,
        "revision": _preset().revision,
        "dimension": 384,
    }


def test_download_is_idempotent_for_an_installed_model(tmp_path: Path) -> None:
    target = _create_installed_model(tmp_path)

    def unexpected_download(**_kwargs: object) -> str:
        raise AssertionError("an installed model must not be downloaded again")

    progress: list[tuple[int, int, str]] = []
    store = _store(tmp_path, unexpected_download)

    assert (
        store.download(
            MINILM_ID,
            progress=lambda current, total, message: progress.append((current, total, message)),
        )
        == target
    )
    assert progress == [(1, 1, "模型已经安装。")]


def test_invalid_snapshot_cleans_only_this_download_staging(tmp_path: Path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    unrelated = root / ".another-task.download"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("owned by another task", encoding="utf-8")

    def incomplete_download(**kwargs: object) -> str:
        destination = Path(str(kwargs["local_dir"]))
        (destination / "config.json").write_text("{}", encoding="utf-8")
        return str(destination)

    store = _store(root, incomplete_download)

    with pytest.raises(ValueError, match="modules.json"):
        store.download(MINILM_ID)

    assert unrelated.is_dir()
    assert (unrelated / "keep.txt").is_file()
    assert [path for path in root.glob("*.download") if path != unrelated] == []
    assert store.installed_path(MINILM_ID) is None


def test_cancel_after_downloader_cleans_staging_without_committing(tmp_path: Path) -> None:
    cancellation_checks = iter((False, True))

    def downloader(**kwargs: object) -> str:
        destination = Path(str(kwargs["local_dir"]))
        (destination / "modules.json").write_text("[]", encoding="utf-8")
        return str(destination)

    store = _store(tmp_path, downloader)

    with pytest.raises(EmbeddingModelDownloadCancelled):
        store.download(MINILM_ID, cancelled=lambda: next(cancellation_checks))

    assert store.installed_path(MINILM_ID) is None
    assert not list(tmp_path.glob("*.download"))


def test_downloader_failure_preserves_existing_invalid_destination(tmp_path: Path) -> None:
    target = tmp_path / MINILM_ID
    target.mkdir(parents=True)
    sentinel = target / "user-file.txt"
    sentinel.write_text("keep", encoding="utf-8")
    store = _store(tmp_path, lambda **_kwargs: "unused")

    with pytest.raises(FileExistsError, match="not a valid installation"):
        store.download(MINILM_ID)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_default_downloader_is_lazy_and_checks_cancellation_during_transfer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import huggingface_hub

    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    def snapshot_download(**kwargs: object) -> str:
        progress_type = kwargs["tqdm_class"]
        progress = progress_type(total=1, disable=True)
        progress.update(1)
        raise AssertionError("cancellation should interrupt the progress update")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)
    store = _store(tmp_path)

    with pytest.raises(EmbeddingModelDownloadCancelled):
        store.download(MINILM_ID, cancelled=cancelled)

    assert not list(tmp_path.glob("*.download"))
    assert store.installed_path(MINILM_ID) is None


def test_default_downloader_forwards_real_progress_without_terminal_bar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import huggingface_hub

    def snapshot_download(**kwargs: object) -> str:
        progress_type = kwargs["tqdm_class"]
        destination = Path(str(kwargs["local_dir"]))
        byte_progress = progress_type(
            total=0,
            desc="Downloading (incomplete total...)",
            unit="B",
            name="huggingface_hub.snapshot_download",
        )
        byte_progress.update(5 * 1024 * 1024)
        files = progress_type(range(3), total=3, desc="tb-progress-probe")
        list(files)
        byte_progress.close()
        (destination / "modules.json").write_text('[{"path": ""}]', encoding="utf-8")
        (destination / "config.json").write_text("{}", encoding="utf-8")
        (destination / "model.safetensors").write_bytes(b"weights")
        return str(destination)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)
    events: list[tuple[int, int, str]] = []
    store = _store(tmp_path)

    installed = store.download(
        MINILM_ID,
        progress=lambda current, total, message: events.append((current, total, message)),
    )

    assert installed == tmp_path / MINILM_ID
    assert any(total == -1 and "5.0 MB" in message for _current, total, message in events)
    assert [(current, total) for current, total, _message in events if total == 3] == [(1, 3), (2, 3), (3, 3)]
    assert events[-2:] == [(0, 0, "模型文件下载完成，正在校验并安装…"), (1, 1, "模型安装完成。")]
    captured = capsys.readouterr()
    assert "tb-progress-probe" not in captured.out
    assert "tb-progress-probe" not in captured.err


def test_remove_only_deletes_a_validated_managed_target(tmp_path: Path) -> None:
    target = _create_installed_model(tmp_path)
    store = _store(tmp_path)

    assert store.remove(MINILM_ID) is True
    assert not target.exists()
    assert store.remove(MINILM_ID) is False

    escaped = replace(_preset(), id="../outside")
    store._presets["../outside"] = escaped
    with pytest.raises(ValueError, match="escapes the managed root"):
        store.remove("../outside")


def test_remove_refuses_an_invalid_known_destination(tmp_path: Path) -> None:
    target = tmp_path / MINILM_ID
    target.mkdir()
    sentinel = target / "user-file.txt"
    sentinel.write_text("keep", encoding="utf-8")
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="unvalidated embedding model path"):
        store.remove(MINILM_ID)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_unknown_model_id_is_rejected_without_creating_storage(tmp_path: Path) -> None:
    store = _store(tmp_path / "models")

    with pytest.raises(KeyError, match="Unknown embedding model preset"):
        store.download("not-a-preset")

    assert not store.root.exists()
