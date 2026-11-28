# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Publish the current source from explicit project-save receipts in commit order."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import json
import logging

from PyQt6.QtCore import QObject, pyqtSignal

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.migration_snapshot import freeze_collection_entries
from transbridge.application.projects.dictionary_apply import dictionary_library, dictionary_mod_id
from transbridge.config.dictionary_preferences import dictionary_preferences
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.translation_memory.library import DictionaryLibrary
from transbridge.ui.workers import ApiWorker

from .dictionary_save_changes import DictionarySaveChanges, source_keys_since

_logger = logging.getLogger(__name__)


@dataclass
class SavedDictionarySource:
    identity: tuple[str, str]
    mod_id: str
    library: DictionaryLibrary
    read: Callable[[int, int, tuple[EntryKey, ...] | None], Iterable[TranslationEntry]]
    namespace: str = ""
    fingerprint: str = ""

    @classmethod
    def capture(cls, context, *, source_key=None):
        source = context if source_key is None else context.slots.get(source_key)
        if source is None:
            return None
        collection = source.collection
        mod_id = dictionary_mod_id(source)
        if collection is None or not mod_id or context.active_version_identity is None:
            return None
        if not dictionary_preferences(context).save_on_project_save:
            return None
        read = getattr(collection, "saved_entries", None)
        if read is not None:
            info = collection.source.info
            return cls(
                context.active_version_identity,
                mod_id,
                dictionary_library(context),
                read,
                info.namespace.value,
                info.fingerprint,
            )
        # In-memory sources have no committed paged reader. Bind their rows to the revision at click time.
        revision = context.variant_revision
        frozen = freeze_collection_entries(collection)

        def read_frozen(_project_revision, variant_revision, _keys):
            if variant_revision != revision:
                raise ValueError("词条版本与工程保存回执不一致")
            return frozen()

        return cls(context.active_version_identity, mod_id, dictionary_library(context), read_frozen)

    def publish(self, receipt, batches):
        if self.identity != (receipt["project_id"], receipt["variant_id"]):
            raise ValueError("活动来源与工程保存回执不一致")
        project_revision, revision = receipt["persisted_project_revision"], receipt["persisted_variant_revision"]
        if not self.fingerprint:
            return self.library.save(self.read(project_revision, revision, None), self.mod_id)
        return self.library.save_revision(
            project_id=self.identity[0],
            variant_id=self.identity[1],
            source_id=json.dumps([self.namespace, self.fingerprint], ensure_ascii=False),
            mod_id=self.mod_id,
            revision=revision,
            project_revision=project_revision,
            changes_since=lambda previous: source_keys_since(batches, previous, revision, self.namespace),
            read=lambda keys: self.read(project_revision, revision, keys),
        )


class SavedDictionaryWriter(QObject):
    changed = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)
        self._worker = None
        self._changes = DictionarySaveChanges()

    @property
    def busy(self):
        return self._worker is not None

    def submit(self, sources, receipt):
        try:
            batches = self._changes.record(receipt)
            if not sources:
                return
            worker = ApiWorker(
                self._write, tuple(sources), receipt, batches, report_api_status=False, route_http_errors=False
            )
            self._worker = worker
            worker.result.connect(self._published)
            worker.finished.connect(self._finished)
            worker.finished.connect(worker.deleteLater)
            worker.start()
        except Exception:
            _logger.exception("无法启动已保存译文的词典写入")
            self._worker = None

    @staticmethod
    def _write(sources, receipt, batches):
        count = 0
        for source in sources:
            try:
                count += source.publish(receipt, batches)
            except Exception:
                _logger.exception("无法将已保存译文存入词典：%s", source.mod_id)
        return count

    def _published(self, count):
        if count:
            self.changed.emit()

    def _finished(self):
        self._worker = None
