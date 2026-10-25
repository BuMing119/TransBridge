"""Verify file imports before adopting their newly allocated entry identities."""

from transbridge.converter.translation_entry import TranslationEntry

from .models import RemoteEntry, RemoteFile


def validate_creation_entries(entries: tuple[TranslationEntry, ...]) -> None:
    if not entries:
        raise ValueError("新建分类文件必须包含真实新增词条，不能上传空 JSON。")
    if any(not entry.key for entry in entries) or len({entry.key for entry in entries}) != len(entries):
        raise ValueError("新建分类文件包含空 key 或重复 key，未执行上传。")


def imported_entries(path: str, entries: tuple[TranslationEntry, ...], created: RemoteFile) -> dict[str, RemoteEntry]:
    """Only adopt exact source content; translation/stage can be completed by ID."""
    validate_creation_entries(entries)
    if type(created.file_id) is not int or created.file_id < 1 or created.path != path:
        raise ValueError("新建文件的身份或路径与计划不一致，再次上传会先自动核验。")
    by_key = {entry.key: entry for entry in created.entries}
    if len(by_key) != len(created.entries) or set(by_key) != {entry.key for entry in entries}:
        raise ValueError("新建文件回读的词条集合与提交内容不一致，本次不会重复创建；再次上传会先自动核验。")
    ids = set()
    for local in entries:
        remote = by_key[local.key]
        if (
            type(remote.remote_id) is not int
            or remote.remote_id < 1
            or remote.remote_id in ids
            or remote.file_id != created.file_id
            or (remote.original, remote.context) != (local.original, local.context or "")
        ):
            raise ValueError("新建文件回读的词条身份或源内容不一致，本次不会重复创建；再次上传会先自动核验。")
        ids.add(remote.remote_id)
    return by_key
