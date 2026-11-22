# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Publish a workbench collection to one current on-disk dictionary."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import json
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from threading import Lock

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.migration_snapshot import detached_entries
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence._utils import atomic_write_json, validate_name

from .manager import DICT_SUFFIX, TranslationMemoryManager
from .model import VALID_SCOPES, Dictionary


@dataclass(frozen=True)
class DictionarySave:
    mod_file_id: str
    scope: str
    tags: tuple[str, ...]
    read: Callable[[], Iterable[TranslationEntry]]


def capture_dictionary_save(
    collection, mod_file_id: str, scope: str, entry_keys: Iterable[EntryKey] | None, tags: list[str]
) -> DictionarySave:
    if not isinstance(mod_file_id, str):
        raise ValueError("词典名称须为字符串。")
    name = validate_name(mod_file_id)
    if ":" in name or PureWindowsPath(name + DICT_SUFFIX).is_reserved():
        raise ValueError("词典名称不能使用保留文件名或驱动器分隔符。")
    if not isinstance(scope, str) or scope not in VALID_SCOPES:
        raise ValueError("词典范围须为 global 或 project。")
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("词典标签须为字符串列表。")
    selected = None if entry_keys is None else tuple(dict.fromkeys(entry_keys))
    if selected == ():
        raise ValueError("没有选中条目。")
    if callable(freeze := getattr(collection, "freeze_entries", None)):
        read_source = freeze()

        def read():
            return read_source(selected)

    else:
        entries = collection if selected is None else (collection.get(key) for key in selected)
        frozen = detached_entries(entry for entry in entries if entry is not None)

        def read():
            return frozen

    return DictionarySave(name, scope, tuple(dict.fromkeys(tag.strip() for tag in tags if tag.strip())), read)


# The two entry points may have loaded their dictionaries at different times.
# Serialize read/merge/stage for these writers. Only the final rename goes through
# the caller's request commit gate; file contents are prepared on its worker.
_publication_lock = Lock()


def publish_dictionary(
    manager: TranslationMemoryManager,
    save: DictionarySave,
    *,
    commit: Callable[[Callable[[], None]], None],
    checkpoint: Callable[[], None],
) -> tuple[int, Dictionary | None]:
    checkpoint()
    entries = save.read()
    with _publication_lock:
        checkpoint()
        key = save.mod_file_id
        target = manager.dictionary_path(key)
        working = TranslationMemoryManager()
        revision = None
        if target.exists():
            current = Dictionary.from_dict(json.loads(target.read_text(encoding="utf-8")))
            if current.mod_file_id != key:
                raise ValueError(f"目标文件保存的是另一部词典：{current.mod_file_id}")
            working.dictionaries[key] = current
            revision = current.revision
        added = working.save_from_collection(entries, key, scope=save.scope, tags=list(save.tags))
        result = working.dictionaries.get(key)
        if result is None or result.revision == revision:
            commit(checkpoint)
        else:
            checkpoint()
            staging_parent = next(parent for parent in target.absolute().parents if parent.is_dir())
            with TemporaryDirectory(dir=staging_parent, prefix=".dictionary-") as staging:
                staged = Path(staging) / target.name
                atomic_write_json(staged, result.to_dict())

                def publish():
                    checkpoint()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    staged.replace(target)

                commit(publish)
        return added, result
