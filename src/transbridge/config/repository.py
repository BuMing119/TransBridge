"""Versioned, atomic owner of the single TransBridge INI file."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
import configparser
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
from io import StringIO
import os
from pathlib import Path
import tempfile
from threading import RLock
import time
from typing import Any

from .paratranz_credentials import (
    CredentialRef,
    CredentialStorageError,
    CredentialStore,
    SecretValue,
    default_credential_store,
)
from .paths import get_config_file_path, get_legacy_config_file_path

CONFIG_SCHEMA_VERSION = 2
_SECRET_KEYS = frozenset({"api_key", "embedding_api_key", "auth_token", "token"})
_ENDPOINT_KEYS = frozenset({"provider", "base_url", "model"})
_PATH_LOCKS: dict[str, RLock] = {}
_PATH_LOCKS_GUARD = RLock()


class ConfigRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ConfigFutureSchemaError(ConfigRepositoryError):
    pass


class ConfigMigrationError(ConfigRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class ConfigSection:
    name: str
    values: tuple[tuple[str, str], ...] = ()

    def get(self, key: str, default: str | None = None) -> str | None:
        return dict(self.values).get(key, default)


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    revision: int
    sections: tuple[ConfigSection, ...]
    path: str
    schema_version: int = CONFIG_SCHEMA_VERSION

    def section(self, name: str) -> ConfigSection:
        for section in self.sections:
            if section.name == name:
                return section
        return ConfigSection(name)

    def value(self, section: str, key: str, default: str | None = None) -> str | None:
        return self.section(section).get(key, default)

    @property
    def fingerprint(self) -> str:
        payload = "\n".join(
            f"{section.name}\0{key}\0{value}" for section in self.sections for key, value in section.values
        )
        return hashlib.sha256(f"{self.schema_version}\0{self.revision}\0{payload}".encode()).hexdigest()


class ConfigRepository:
    """Own ConfigParser, locking, migration and atomic replacement."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        legacy_path: str | os.PathLike[str] | None = None,
        credential_store: CredentialStore | None = None,
        replace_func: Callable[[str | os.PathLike[str], str | os.PathLike[str]], None] = os.replace,
    ) -> None:
        self.path = Path(path or get_config_file_path())
        self.legacy_path = Path(legacy_path or get_legacy_config_file_path())
        self.credential_store = credential_store or default_credential_store()
        self._replace = replace_func
        resolved = os.path.normcase(os.path.abspath(self.path))
        with _PATH_LOCKS_GUARD:
            self._lock = _PATH_LOCKS.setdefault(resolved, RLock())

    def load(self) -> ConfigSnapshot:
        with self._lock, self._file_lock():
            self._ensure_migrated_locked()
            return self._read_snapshot_locked()

    def update_sections(
        self,
        updates: Mapping[str, Mapping[str, Any | None]],
    ) -> ConfigSnapshot:
        llm_keys = set(updates.get("llm", {})) & _ENDPOINT_KEYS
        if llm_keys and llm_keys != _ENDPOINT_KEYS:
            raise ConfigRepositoryError(
                "config_endpoint_group_incomplete",
                "provider, base_url and model must be updated atomically",
            )
        if llm_keys and any(not str(updates["llm"][key]).strip() for key in _ENDPOINT_KEYS):
            raise ConfigRepositoryError(
                "config_endpoint_group_invalid",
                "provider, base_url and model must not be empty",
            )
        for section, values in updates.items():
            forbidden = set(values) & _SECRET_KEYS
            if forbidden:
                names = ", ".join(sorted(forbidden))
                raise ConfigRepositoryError(
                    "config_plaintext_secret_forbidden",
                    f"plaintext secret keys are forbidden in INI: {section}.{names}",
                )

        with self._lock, self._file_lock():
            self._ensure_migrated_locked()
            parser = self._read_parser(self.path) if self.path.exists() else self._new_parser()
            current_revision = self._validated_revision(parser) if self.path.exists() else 0
            for section, values in updates.items():
                if not parser.has_section(section):
                    parser.add_section(section)
                for key, value in values.items():
                    if value is None:
                        parser.remove_option(section, key)
                    else:
                        parser.set(section, key, _config_value(value))
            self._set_meta(parser, current_revision + 1)
            self._validate_no_secrets(parser)
            self._atomic_write_parser(parser, self.path)
            snapshot = self._read_snapshot_locked()
            if snapshot.revision != current_revision + 1:
                raise ConfigRepositoryError(
                    "config_revision_verify_failed", "configuration revision verification failed"
                )
            return snapshot

    def _ensure_migrated_locked(self) -> None:
        if self.path.exists():
            parser = self._read_parser(self.path)
            if parser.has_section("meta"):
                self._validated_revision(parser)
                return
            self._migrate_legacy_locked(self.path, in_place=True)
            return
        if self.legacy_path.exists():
            self._migrate_legacy_locked(self.legacy_path, in_place=False)

    def _migrate_legacy_locked(self, source: Path, *, in_place: bool) -> None:
        original = source.read_bytes()
        legacy = self._read_parser(source)
        if legacy.has_section("meta"):
            raise ConfigMigrationError(
                "config_legacy_unexpected_schema", "legacy source unexpectedly contains schema metadata"
            )

        migrated = self._new_parser()
        for section in legacy.sections():
            if section == "llm_profiles":
                continue
            target = {"api": "paratranz", "headers": "paratranz.headers"}.get(section, section)
            if not migrated.has_section(target):
                migrated.add_section(target)
            for key, value in legacy.items(section, raw=True):
                if key not in _SECRET_KEYS:
                    migrated.set(target, key, value)

        secret_specs = (
            ("api", "token", "paratranz", "credential_ref", CredentialRef()),
            ("llm", "api_key", "llm", "credential_ref", CredentialRef("TransBridge.LLM", "default")),
            (
                "llm",
                "embedding_api_key",
                "llm",
                "embedding_credential_ref",
                CredentialRef("TransBridge.Embedding", "default"),
            ),
            ("mcp", "auth_token", "mcp", "credential_ref", CredentialRef("TransBridge.MCP", "default")),
        )
        try:
            for old_section, old_key, new_section, new_key, reference in secret_specs:
                value = legacy.get(old_section, old_key, fallback="", raw=True).strip()
                if value:
                    self._store_verified(reference, value)
                if value or legacy.has_option(old_section, old_key):
                    if not migrated.has_section(new_section):
                        migrated.add_section(new_section)
                    migrated.set(new_section, new_key, reference.target_name)
        except CredentialStorageError as exc:
            raise ConfigMigrationError(
                "config_secret_migration_failed",
                "legacy secret migration could not be verified",
            ) from exc

        self._set_meta(migrated, 1)
        migrated.set("meta", "legacy_sha256", hashlib.sha256(original).hexdigest())
        migrated.set("meta", "migrated_from", source.name)
        self._validate_no_secrets(migrated)
        payload = self._serialize(migrated)
        self._validate_payload(payload)

        backup = source.with_name(f"{source.name}.validated.bak")
        self._atomic_write_bytes(payload, backup)
        target = source if in_place else self.path
        self._atomic_write_bytes(payload, target)
        verified = self._read_parser(target)
        self._validated_revision(verified)
        self._validate_no_secrets(verified)
        if not in_place:
            source.unlink()

    def _store_verified(self, reference: CredentialRef, value: str) -> None:
        if not self.credential_store.capability.writable:
            raise CredentialStorageError("secure credential storage is not writable")
        secret = SecretValue(value)
        self.credential_store.set(reference, secret)
        verified = self.credential_store.get(reference)
        if verified is None or not hmac.compare_digest(verified._reveal_for_request(), value):
            raise CredentialStorageError("secure credential verification failed")

    def _read_snapshot_locked(self) -> ConfigSnapshot:
        if not self.path.exists():
            return ConfigSnapshot(0, (), str(self.path))
        parser = self._read_parser(self.path)
        revision = self._validated_revision(parser)
        self._validate_no_secrets(parser)
        sections = tuple(
            ConfigSection(
                section,
                tuple(sorted(parser.items(section, raw=True))),
            )
            for section in sorted(parser.sections())
            if section != "meta"
        )
        return ConfigSnapshot(revision, sections, str(self.path))

    @staticmethod
    def _new_parser() -> configparser.ConfigParser:
        return configparser.ConfigParser(interpolation=None)

    @classmethod
    def _read_parser(cls, path: Path) -> configparser.ConfigParser:
        parser = cls._new_parser()
        try:
            with path.open("r", encoding="utf-8") as stream:
                parser.read_file(stream)
        except (configparser.Error, UnicodeDecodeError) as exc:
            raise ConfigRepositoryError("config_invalid_ini", "configuration file is invalid") from exc
        return parser

    @staticmethod
    def _set_meta(parser: configparser.ConfigParser, revision: int) -> None:
        if not parser.has_section("meta"):
            parser.add_section("meta")
        parser.set("meta", "schema_version", str(CONFIG_SCHEMA_VERSION))
        parser.set("meta", "revision", str(revision))

    @staticmethod
    def _validated_revision(parser: configparser.ConfigParser) -> int:
        if not parser.has_section("meta"):
            raise ConfigRepositoryError("config_schema_missing", "configuration schema metadata is missing")
        try:
            schema = parser.getint("meta", "schema_version")
            revision = parser.getint("meta", "revision")
        except (ValueError, configparser.Error) as exc:
            raise ConfigRepositoryError("config_meta_invalid", "configuration metadata is invalid") from exc
        if schema > CONFIG_SCHEMA_VERSION:
            raise ConfigFutureSchemaError(
                "config_future_schema",
                f"configuration schema {schema} is newer than supported {CONFIG_SCHEMA_VERSION}",
            )
        if schema != CONFIG_SCHEMA_VERSION or revision < 0:
            raise ConfigRepositoryError("config_schema_invalid", "configuration schema is unsupported")
        return revision

    @staticmethod
    def _validate_no_secrets(parser: configparser.ConfigParser) -> None:
        leaks = [
            f"{section}.{key}"
            for section in parser.sections()
            for key, value in parser.items(section, raw=True)
            if key in _SECRET_KEYS and value.strip()
        ]
        if leaks:
            raise ConfigRepositoryError(
                "config_plaintext_secret_detected",
                f"plaintext secrets are forbidden: {', '.join(leaks)}",
            )

    @classmethod
    def _validate_payload(cls, payload: bytes) -> None:
        parser = cls._new_parser()
        try:
            parser.read_string(payload.decode("utf-8"))
        except (UnicodeDecodeError, configparser.Error) as exc:
            raise ConfigRepositoryError("config_serialization_invalid", "serialized configuration is invalid") from exc
        cls._validated_revision(parser)
        cls._validate_no_secrets(parser)

    @staticmethod
    def _serialize(parser: configparser.ConfigParser) -> bytes:
        stream = StringIO()
        parser.write(stream)
        return stream.getvalue().encode("utf-8")

    def _atomic_write_parser(self, parser: configparser.ConfigParser, path: Path) -> None:
        self._atomic_write_bytes(self._serialize(parser), path)

    def _atomic_write_bytes(self, payload: bytes, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._replace_with_retry(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _replace_with_retry(self, source: Path, target: Path) -> None:
        """Retry only transient Windows sharing violations; keep replace atomic."""
        attempts = 5 if os.name == "nt" else 1
        for attempt in range(attempts):
            try:
                self._replace(source, target)
                return
            except PermissionError as exc:
                winerror = getattr(exc, "winerror", None)
                transient = os.name == "nt" and (winerror in {5, 32} or (winerror is None and exc.errno in {5, 13}))
                if not transient or attempt == attempts - 1:
                    raise
                time.sleep(0.02 * (attempt + 1))

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        with lock_path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def default_config_repository(*, credential_store: CredentialStore | None = None) -> ConfigRepository:
    return ConfigRepository(credential_store=credential_store)


def _config_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "ConfigFutureSchemaError",
    "ConfigMigrationError",
    "ConfigRepository",
    "ConfigRepositoryError",
    "ConfigSection",
    "ConfigSnapshot",
    "default_config_repository",
]
