"""Explicit, verified recovery export for a project that cannot be saved."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from zipfile import ZipFile

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from transbridge.converter.translation_entry_collection import TranslationEntryCollection


@dataclass(frozen=True)
class RecoverySource:
    key: str
    label: str
    collection: TranslationEntryCollection
    paths: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True)
class RecoveryCapture:
    stamp: tuple
    sources: tuple[RecoverySource, ...]
    authority: object
    labels: dict


class SaveRecovery:
    """Authorize exit only for the unchanged state covered by a verified export."""

    def __init__(self, context) -> None:
        self._context = context
        self._generation = 0
        self._receipt = None
        for name in (
            "dirty_changed",
            "collection_changed",
            "collection_list_changed",
            "project_changed",
            "variant_changed",
            "label_data_changed",
        ):
            signal = getattr(context, name, None)
            if signal is not None:
                signal.connect(self._changed)

    def _changed(self, *_args) -> None:
        self._generation += 1

    def _stamp(self) -> tuple:
        context = self._context
        return (
            self._generation,
            getattr(context, "active_project_id", None),
            getattr(context, "active_variant_id", None),
            getattr(context, "project_revision", None),
            getattr(context, "variant_revision", None),
            getattr(context, "active_key", None),
            tuple(
                (key, id(slot.collection), slot.collection.collection_revision.value)
                for key, slot in context.slots.items()
            ),
        )

    def reset(self) -> None:
        self._receipt = None

    @property
    def has_backup(self) -> bool:
        return self._receipt is not None

    @property
    def verified(self) -> bool:
        return self.has_backup and self._receipt == self._stamp()

    def capture(self) -> RecoveryCapture:
        context = self._context
        projection = getattr(context, "_project_projection", None)
        authority = None if projection is None else projection.snapshot()
        return RecoveryCapture(
            self._stamp(),
            tuple(
                RecoverySource(
                    key,
                    slot.label,
                    slot.collection,
                    tuple(
                        (name, getattr(slot, name, None))
                        for name in ("esp_path", "eet_path", "xt_path", "strings_path", "sst_path")
                    ),
                )
                for key, slot in context.slots.items()
            ),
            authority,
            {
                "library": getattr(context, "label_library", {}),
                "entries": {key: sorted(labels) for key, labels in getattr(context, "_entry_labels", {}).items()},
                "exact_entries": [
                    {"identity": list(key), "labels": sorted(labels)}
                    for key, labels in getattr(context, "_entry_labels_exact", {}).items()
                ],
            },
        )

    def accept(self, capture: RecoveryCapture) -> bool:
        if capture.stamp != self._stamp():
            return False
        self._receipt = capture.stamp
        return True


def choose_recovery_destination(host) -> Path | None:
    dialog = QMessageBox(host)
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setWindowTitle("项目尚未保存")
    dialog.setText("保存失败或仍有未保存的更改。可以返回工作台重试，或先备份当前全部翻译内容再退出。")
    dialog.setInformativeText(
        "备份包含所有已打开来源的译文、条目元数据和工程状态。原始插件文件需另行保留；恢复方法会写入备份包。"
    )
    stay = dialog.addButton("返回工作台", QMessageBox.ButtonRole.RejectRole)
    backup = dialog.addButton("备份当前内容并退出", QMessageBox.ButtonRole.AcceptRole)
    dialog.setDefaultButton(stay)
    dialog.setEscapeButton(stay)
    dialog.exec()
    if dialog.clickedButton() is not backup:
        return None
    filename, _ = QFileDialog.getSaveFileName(
        host,
        "保存恢复备份后退出",
        f"TransBridge-recovery-{datetime.now():%Y%m%d-%H%M%S}.zip",
        "恢复备份 (*.zip)",
    )
    return Path(filename) if filename else None


def write_recovery_bundle(destination: Path, capture: RecoveryCapture) -> Path:
    """Write every visible source using the existing lossless internal JSON format."""
    destination = Path(destination)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".transbridge-recovery-", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w+b") as stream:
            checksums = {}
            sources = []
            with ZipFile(stream, "w") as archive:
                for index, source in enumerate(capture.sources, 1):
                    member = f"sources/{index:04d}.json"
                    content = source.collection.to_json().encode("utf-8")
                    archive.writestr(member, content)
                    checksums[member] = hashlib.sha256(content).hexdigest()
                    sources.append({
                        "key": source.key,
                        "label": source.label,
                        "file": member,
                        "paths": dict(source.paths),
                    })
                manifest = {
                    "format": "transbridge-recovery-v1",
                    "sources": sources,
                    "labels": capture.labels,
                    "authority": None if capture.authority is None else capture.authority.to_dict(),
                    "sha256": checksums,
                }
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                archive.writestr(
                    "恢复说明.txt",
                    "这是保存失败时导出的恢复副本，不代表原工程已保存成功。\n"
                    "先解压备份。manifest.json 的 sources 记录每个来源及对应的 sources/*.json。\n"
                    "重新打开原工程或用保留的原始文件创建工程，选择对应来源，使用“导入已有译文”导入其 JSON。\n"
                    "各 JSON 使用 TransBridge 完整内部格式，保留原文、译文、状态、身份及条目元数据。\n"
                    "manifest.json 还保留标签和工程状态，供进一步恢复；普通译文导入不会自动恢复这些工程设置。\n"
                    "若原工程已受损，请保留此备份和原始文件，在新工程中恢复。\n",
                )
            stream.flush()
            os.fsync(stream.fileno())
        with ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise OSError("恢复备份校验失败")
            verified = json.loads(archive.read("manifest.json"))
            for member, checksum in verified["sha256"].items():
                if hashlib.sha256(archive.read(member)).hexdigest() != checksum:
                    raise OSError(f"恢复备份内容校验失败：{member}")
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
