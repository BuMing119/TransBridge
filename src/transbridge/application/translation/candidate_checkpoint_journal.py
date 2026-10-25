"""Crash-safe, checksummed records and a small atomic checkpoint commit head."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets


def encode(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def signature(path: Path) -> tuple | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@contextmanager
def storage_lease(target: Path):
    """Serialize cooperating readers/writers across both ports and processes."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.with_suffix(".lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if not stream.tell():
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("translation checkpoint is corrupt") from exc
    if not isinstance(value, dict):
        raise ValueError("translation checkpoint root must be an object")
    return value


def journal_path(target: Path, head: dict) -> Path:
    name = head.get("journal")
    prefix = target.stem + "-"
    if not isinstance(name, str) or not name.startswith(prefix) or not name.endswith(".jsonl"):
        raise ValueError("translation checkpoint journal identity is invalid")
    token = name[len(prefix) : -6]
    if len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
        raise ValueError("translation checkpoint journal identity is invalid")
    return target.parent / name


def read_records(target: Path, head: dict):
    """Ignore only an uncommitted suffix; every committed byte is validated."""
    offset = head.get("offset")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset <= 0:
        raise ValueError("translation checkpoint journal offset is invalid")
    previous = ""
    try:
        with journal_path(target, head).open("rb") as stream:
            while stream.tell() < offset:
                line = stream.readline(offset - stream.tell())
                if not line.endswith(b"\n"):
                    raise ValueError("translation checkpoint journal is truncated")
                envelope = json.loads(line)
                record = envelope["record"]
                digest = hashlib.sha256(encode({"previous": previous, "record": record})).hexdigest()
                if envelope["previous"] != previous or envelope["sha256"] != digest:
                    raise ValueError("translation checkpoint journal checksum mismatch")
                previous = digest
                yield record
            if stream.tell() != offset or previous != head.get("sha256"):
                raise ValueError("translation checkpoint journal commit head mismatch")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("translation checkpoint journal is corrupt") from exc


def write_record(path: Path, record: dict, *, offset: int = 0, previous: str = "") -> tuple[int, str]:
    body = {"previous": previous, "record": record}
    digest = hashlib.sha256(encode(body)).hexdigest()
    payload = encode({**body, "sha256": digest}) + b"\n"
    descriptor = os.open(path, os.O_RDWR | (0 if offset else os.O_CREAT | os.O_EXCL), 0o600)
    with os.fdopen(descriptor, "r+b") as stream:
        stream.seek(offset)
        stream.truncate()  # Discard a suffix whose commit head was never published.
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        stream.seek(offset)
        if stream.read(len(payload)) != payload:
            raise ValueError("translation checkpoint journal write verification failed")
    return offset + len(payload), digest


def publish_head(target: Path, head: dict) -> None:
    payload = encode(head)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.read_bytes() != payload:
            raise ValueError("translation checkpoint commit head write verification failed")
        os.replace(temporary, target)
        fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
