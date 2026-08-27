"""Managed storage for downloadable local embedding models."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
from threading import Lock
from typing import Protocol

from transbridge.config.paths import get_data_dir
from transbridge.infra.embedding_model_catalog import EmbeddingModelPreset, load_embedding_model_catalog

# ``total > 0`` is determinate progress, ``total == 0`` is an
# indeterminate phase, and ``total < 0`` updates only the status message.
ProgressCallback = Callable[[int, int, str], None]
CancellationCallback = Callable[[], bool]


class SnapshotDownloader(Protocol):
    """Subset of ``huggingface_hub.snapshot_download`` used by the store."""

    def __call__(
        self,
        *,
        repo_id: str,
        revision: str,
        local_dir: str,
        ignore_patterns: Sequence[str],
    ) -> str | Path: ...


@dataclass(frozen=True, slots=True)
class EmbeddingModelState:
    """Installation state for a preset model."""

    preset: EmbeddingModelPreset
    path: Path
    installed: bool


_INSTALL_MANIFEST = "transbridge-model.json"
_INSTALL_SCHEMA_VERSION = 1

_IGNORED_SNAPSHOT_PATTERNS: tuple[str, ...] = (
    "*.onnx",
    "onnx/*",
    "onnx/**/*",
    "openvino/*",
    "openvino/**/*",
    "tf_model.h5",
    "flax_model.msgpack",
    "pytorch_model.bin",
)


class EmbeddingModelDownloadCancelled(RuntimeError):
    """Raised when a local embedding model download is cancelled."""


class EmbeddingModelStore:
    """Own and manage application-downloaded embedding model snapshots."""

    def __init__(
        self,
        root: str | Path | None = None,
        downloader: SnapshotDownloader | None = None,
        catalog_path: str | Path | None = None,
    ) -> None:
        configured_root = Path(root) if root is not None else Path(get_data_dir()) / "models" / "embedding"
        self._root = configured_root.expanduser().resolve(strict=False)
        self._downloader = downloader
        self._preset_order = load_embedding_model_catalog(catalog_path)
        self._presets = {preset.id: preset for preset in self._preset_order}

    @property
    def root(self) -> Path:
        return self._root

    def list_models(self) -> tuple[EmbeddingModelState, ...]:
        """Return every preset in display order with its current installation state."""
        return tuple(
            EmbeddingModelState(
                preset=preset,
                path=self._model_path(preset.id),
                installed=self._is_installed(preset.id),
            )
            for preset in self._preset_order
        )

    def installed_path(self, model_id: str) -> Path | None:
        """Return the managed model directory when the preset is installed."""
        self._get_preset(model_id)
        path = self._model_path(model_id)
        return path if self._is_installed(model_id) else None

    def model_identity(self, model_id: str) -> dict[str, object] | None:
        """Return the validated, secret-free identity for an installed preset."""

        preset = self._get_preset(model_id)
        path = self.installed_path(model_id)
        if path is None:
            return None
        return self._read_manifest(path, preset)

    def download(
        self,
        model_id: str,
        progress: ProgressCallback | None = None,
        cancelled: CancellationCallback | None = None,
    ) -> Path:
        """Download, validate, and atomically install a preset model.

        Downloads are written to a unique staging directory owned by this call.
        A failed or cancelled operation only removes that directory.
        """
        preset = self._get_preset(model_id)
        target = self._model_path(model_id)
        installed = self.installed_path(model_id)
        if installed is not None:
            self._report_progress(progress, 1, 1, "模型已经安装。")
            return installed
        if target.exists():
            raise FileExistsError(f"Embedding model destination exists but is not a valid installation: {target}")
        self._raise_if_cancelled(cancelled)

        self._root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{preset.id}-", suffix=".download", dir=self._root))
        staging = self._require_within_root(staging)
        committed = False
        try:
            self._report_progress(progress, 0, 0, "正在连接 Hugging Face 并获取模型文件信息…")
            download_args = {
                "repo_id": preset.repo_id,
                "revision": preset.revision,
                "local_dir": os.fspath(staging),
                "ignore_patterns": _IGNORED_SNAPSHOT_PATTERNS,
            }
            if self._downloader is None:
                self._download_snapshot(**download_args, progress=progress, cancelled=cancelled)
            else:
                self._downloader(**download_args)
            self._raise_if_cancelled(cancelled)
            self._report_progress(progress, 0, 0, "模型文件下载完成，正在校验并安装…")
            self._validate_model_files(staging)
            self._write_manifest(staging, preset)
            self._validate_snapshot(staging, preset)

            if target.exists():
                concurrent_install = self.installed_path(model_id)
                if concurrent_install is not None:
                    return concurrent_install
                raise FileExistsError(f"Embedding model destination was created during download: {target}")

            os.replace(staging, target)
            committed = True
            self._report_progress(progress, 1, 1, "模型安装完成。")
            return target
        finally:
            if not committed and staging.exists():
                shutil.rmtree(staging)

    def remove(self, model_id: str) -> bool:
        """Remove a managed preset model, returning whether anything was removed."""
        preset = self._get_preset(model_id)
        target = self._model_path(model_id)
        if not target.exists() and not target.is_symlink():
            return False
        if target.is_symlink():
            raise ValueError(f"Refusing to remove a symbolic-link model path: {target}")
        try:
            self._validate_snapshot(target, preset)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Refusing to remove an unvalidated embedding model path: {target}") from exc

        checked_target = self._require_within_root(target)
        if checked_target.is_dir():
            shutil.rmtree(checked_target)
        else:
            checked_target.unlink()
        return True

    def _get_preset(self, model_id: str) -> EmbeddingModelPreset:
        try:
            return self._presets[model_id]
        except KeyError as exc:
            raise KeyError(f"Unknown embedding model preset: {model_id}") from exc

    def _model_path(self, model_id: str) -> Path:
        return self._require_within_root(self._root / model_id)

    def _is_installed(self, model_id: str) -> bool:
        path = self._model_path(model_id)
        if path.is_symlink() or not path.is_dir():
            return False
        try:
            self._validate_snapshot(path, self._get_preset(model_id))
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        return True

    def _require_within_root(self, path: Path) -> Path:
        resolved = path.resolve(strict=False)
        if resolved == self._root or not resolved.is_relative_to(self._root):
            raise ValueError(f"Embedding model path escapes the managed root: {path}")
        return resolved

    @staticmethod
    def _raise_if_cancelled(cancelled: CancellationCallback | None) -> None:
        if cancelled is not None and cancelled():
            raise EmbeddingModelDownloadCancelled("Embedding model download was cancelled")

    @staticmethod
    def _report_progress(
        progress: ProgressCallback | None,
        current: int,
        total: int,
        message: str,
    ) -> None:
        if progress is not None:
            progress(current, total, message)

    @staticmethod
    def _validate_model_files(path: Path) -> None:
        modules_file = path / "modules.json"
        if not modules_file.is_file():
            raise ValueError("Downloaded embedding model is incomplete: modules.json is missing")
        modules = json.loads(modules_file.read_text(encoding="utf-8"))
        if not isinstance(modules, list) or not modules:
            raise ValueError("Downloaded embedding model is incomplete: modules.json has no modules")
        for module in modules:
            if not isinstance(module, dict):
                raise ValueError("Downloaded embedding model is incomplete: invalid module declaration")
            module_path = str(module.get("path", "")).strip()
            if module_path and not (path / module_path).is_dir():
                raise ValueError(f"Downloaded embedding model is incomplete: module path is missing: {module_path}")
        if not (path / "config.json").is_file():
            raise ValueError("Downloaded embedding model is incomplete: config.json is missing")
        if not any(
            candidate.is_file()
            for candidate in (
                path / "model.safetensors",
                path / "model.safetensors.index.json",
            )
        ):
            raise ValueError("Downloaded embedding model is incomplete: safetensors weights are missing")

    @classmethod
    def _validate_snapshot(cls, path: Path, preset: EmbeddingModelPreset) -> None:
        cls._validate_model_files(path)
        cls._read_manifest(path, preset)

    @staticmethod
    def _manifest_payload(preset: EmbeddingModelPreset) -> dict[str, object]:
        return {
            "schema_version": _INSTALL_SCHEMA_VERSION,
            "status": "installed",
            "model_id": preset.id,
            "repo_id": preset.repo_id,
            "revision": preset.revision,
            "dimension": preset.dimension,
        }

    @classmethod
    def _write_manifest(cls, path: Path, preset: EmbeddingModelPreset) -> None:
        manifest = path / _INSTALL_MANIFEST
        manifest.write_text(
            json.dumps(cls._manifest_payload(preset), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    @classmethod
    def _read_manifest(cls, path: Path, preset: EmbeddingModelPreset) -> dict[str, object]:
        manifest = path / _INSTALL_MANIFEST
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if payload != cls._manifest_payload(preset):
            raise ValueError(f"Embedding model install manifest does not match preset: {preset.id}")
        return payload

    @staticmethod
    def _download_snapshot(
        *,
        repo_id: str,
        revision: str,
        local_dir: str,
        ignore_patterns: Sequence[str],
        progress: ProgressCallback | None,
        cancelled: CancellationCallback | None,
    ) -> str | Path:
        from huggingface_hub import snapshot_download
        from huggingface_hub.utils import tqdm as huggingface_tqdm

        progress_lock = Lock()
        last_reported_bytes = 0

        def report(current: int, total: int, message: str) -> None:
            if progress is None:
                return
            with progress_lock:
                progress(current, total, message)

        class CancellableTqdm(huggingface_tqdm):
            def __init__(self, *args: object, **kwargs: object) -> None:
                EmbeddingModelStore._raise_if_cancelled(cancelled)
                self._reports_downloaded_bytes = kwargs.get("name") == "huggingface_hub.snapshot_download"
                self._reports_completed_files = not self._reports_downloaded_bytes and kwargs.get("unit") != "B"
                # Keep tqdm's counters active for UI reporting but prevent it from
                # writing a second, terminal-only progress display.
                kwargs["disable"] = False
                super().__init__(*args, **kwargs)

            def update(self, n: int | float = 1) -> bool | None:
                nonlocal last_reported_bytes

                EmbeddingModelStore._raise_if_cancelled(cancelled)
                if self._reports_downloaded_bytes:
                    with progress_lock:
                        result = super().update(n)
                        downloaded = max(0, int(self.n))
                        if progress is not None and (
                            last_reported_bytes == 0 or downloaded - last_reported_bytes >= 4 * 1024 * 1024
                        ):
                            last_reported_bytes = downloaded
                            progress(0, -1, f"正在下载模型文件… 已接收 {EmbeddingModelStore._format_bytes(downloaded)}")
                else:
                    result = super().update(n)
                EmbeddingModelStore._raise_if_cancelled(cancelled)
                return result

            def __iter__(self):
                if not self._reports_completed_files:
                    yield from super().__iter__()
                    return

                total = max(0, int(self.total or 0))
                completed = 0
                for item in super().__iter__():
                    EmbeddingModelStore._raise_if_cancelled(cancelled)
                    yield item
                    completed += 1
                    if total > 0:
                        report(completed, total, f"正在下载模型文件（已完成 {completed}/{total}）…")
                    EmbeddingModelStore._raise_if_cancelled(cancelled)

            def display(self, *_args: object, **_kwargs: object) -> None:
                """Suppress tqdm terminal output; progress is rendered by Qt."""

        return snapshot_download(
            repo_id=repo_id,
            revision=revision,
            local_dir=local_dir,
            ignore_patterns=list(ignore_patterns),
            tqdm_class=CancellableTqdm,
        )

    @staticmethod
    def _format_bytes(value: int) -> str:
        if value >= 1024 * 1024:
            return f"{value / (1024 * 1024):.1f} MB"
        if value >= 1024:
            return f"{value / 1024:.1f} KB"
        return f"{value} B"


__all__ = [
    "EmbeddingModelDownloadCancelled",
    "EmbeddingModelPreset",
    "EmbeddingModelState",
    "EmbeddingModelStore",
]
