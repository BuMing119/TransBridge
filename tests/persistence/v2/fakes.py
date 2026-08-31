"""In-memory, fault-injectable persistence filesystem."""

from __future__ import annotations

import os


class MemoryFilesystem:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.directories: set[str] = set()
        self.canonical_aliases: dict[str, str] = {}
        self.fail_replace_destinations: set[str] = set()
        self.fail_durable_replace_destinations: set[str] = set()
        self.fail_write_paths: set[str] = set()
        self.fail_read_paths: set[str] = set()
        self.fail_list_paths: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def canonicalize(self, path: str) -> str:
        canonical = os.path.normpath(path)
        for source, destination in sorted(self.canonical_aliases.items(), key=lambda item: len(item[0]), reverse=True):
            normalized_source = os.path.normpath(source)
            if os.path.normcase(canonical) == os.path.normcase(normalized_source):
                return os.path.normpath(destination)
            prefix = normalized_source + os.sep
            if os.path.normcase(canonical).startswith(os.path.normcase(prefix)):
                suffix = canonical[len(prefix) :]
                return os.path.normpath(os.path.join(destination, suffix))
        return canonical

    def exists(self, path: str) -> bool:
        canonical = self.canonicalize(path)
        self.calls.append(("exists", canonical))
        return canonical in self.files

    def read_bytes(self, path: str) -> bytes:
        canonical = self.canonicalize(path)
        self.calls.append(("read", canonical))
        if canonical in self.fail_read_paths:
            raise OSError("injected read fault")
        return self.files[canonical]

    def list_files(self, directory: str) -> tuple[str, ...]:
        canonical = self.canonicalize(directory)
        self.calls.append(("list", canonical))
        if canonical in self.fail_list_paths:
            raise OSError("injected list fault")
        files = (path for path in self.files if os.path.normcase(os.path.dirname(path)) == os.path.normcase(canonical))
        return tuple(sorted(files, key=os.path.normcase))

    def make_dirs(self, path: str) -> None:
        canonical = self.canonicalize(path)
        self.calls.append(("mkdir", canonical))
        self.directories.add(canonical)

    def write_bytes(self, path: str, data: bytes) -> None:
        canonical = self.canonicalize(path)
        self.calls.append(("write", canonical))
        if canonical in self.fail_write_paths:
            raise OSError("injected write fault")
        if canonical in self.files:
            raise FileExistsError(canonical)
        self.files[canonical] = bytes(data)

    def replace(self, source: str, destination: str) -> None:
        canonical_source = self.canonicalize(source)
        canonical_destination = self.canonicalize(destination)
        self.calls.append(("replace", canonical_destination))
        if canonical_destination in self.fail_replace_destinations:
            raise OSError("injected replace fault")
        self.files[canonical_destination] = self.files.pop(canonical_source)

    def replace_durable(self, source: str, destination: str) -> None:
        canonical_source = self.canonicalize(source)
        canonical_destination = self.canonicalize(destination)
        self.calls.append(("replace-durable", canonical_destination))
        if canonical_destination in self.fail_durable_replace_destinations:
            raise OSError("injected durable replace fault")
        self.files[canonical_destination] = self.files.pop(canonical_source)

    def remove(self, path: str, *, missing_ok: bool = False) -> None:
        canonical = self.canonicalize(path)
        self.calls.append(("remove", canonical))
        if canonical not in self.files and not missing_ok:
            raise FileNotFoundError(canonical)
        self.files.pop(canonical, None)

    def seed(self, path: str, data: bytes) -> None:
        self.files[self.canonicalize(path)] = bytes(data)
