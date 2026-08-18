"""ParaTranz API configuration without plaintext credential persistence."""

from __future__ import annotations

from collections.abc import Mapping
import hmac
import os
from pathlib import Path

from .paratranz_credentials import (
    CredentialRef,
    CredentialStorageError,
    CredentialStore,
    EnvironmentCredentialProvider,
    SecretStoreCapability,
    SecretValue,
    default_credential_store,
    redact_credential_data,
)
from .paths import get_config_file_path, get_data_dir
from .repository import ConfigRepository, default_config_repository


class ParatranzConfig:
    """ParaTranz settings and a compatibility facade over secure credentials."""

    DEFAULT_BASE_URL = "https://paratranz.cn/api"
    DEFAULT_TIMEOUT = 30
    DEFAULT_HEADERS: dict[str, str] = {}
    DEFAULT_CONFIG_FILE = "transbridge.ini"

    def __init__(
        self,
        token: str | None = None,
        user_id: int | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        extra_headers: Mapping[str, str] | None = None,
        *,
        credential_ref: CredentialRef | None = None,
        credential_store: CredentialStore | None = None,
        environment: Mapping[str, str] | None = None,
        config_path: str | os.PathLike[str] | None = None,
        repository: ConfigRepository | None = None,
    ) -> None:
        self.user_id = user_id
        self.base_url = base_url
        self.timeout = timeout
        self.headers = self.DEFAULT_HEADERS.copy()
        if extra_headers:
            self.headers.update(
                (key, value) for key, value in extra_headers.items() if key.casefold() != "authorization"
            )
        self.credential_ref = credential_ref or CredentialRef()
        self._credential_store = credential_store or default_credential_store()
        self._environment_provider = EnvironmentCredentialProvider(environment)
        self._config_path = Path(config_path) if config_path is not None else Path(get_config_file_path())
        self._repository = repository or (
            ConfigRepository(
                path=self._config_path,
                legacy_path=self._config_path,
                credential_store=self._credential_store,
            )
            if config_path is not None
            else default_config_repository(credential_store=self._credential_store)
        )
        self.config_revision = 0
        self._secret: SecretValue | None = None
        self._secret_source = "missing"
        self.credential_capability = self._credential_store.capability
        env_secret = self._environment_provider.get(self.credential_ref)
        if env_secret is not None:
            self._secret = env_secret
            self._secret_source = "environment"
        elif token:
            self._secret = SecretValue(token)
            self._secret_source = "memory"
        else:
            try:
                self._secret = self._credential_store.get(self.credential_ref)
                if self._secret is not None:
                    self._secret_source = "store"
            except CredentialStorageError:
                self.credential_capability = SecretStoreCapability(
                    False, False, degraded=True, reason_code="SECURE_STORE_READ_FAILED"
                )

    def __repr__(self) -> str:
        safe_base_url = redact_credential_data(self.base_url, self._secret)
        return (
            f"ParatranzConfig(base_url={safe_base_url!r}, timeout={self.timeout!r}, "
            f"user_id={self.user_id!r}, credential_ref={self.credential_ref!r}, token=***)"
        )

    @property
    def token(self) -> str | None:
        """Legacy facade; callers must not log or persist the returned value."""
        return self._secret._reveal_for_request() if self._secret is not None else None

    @staticmethod
    def get_data_dir() -> str:
        return get_data_dir()

    @staticmethod
    def get_config_file_path() -> str:
        return get_config_file_path()

    def get_headers(self) -> dict[str, str]:
        """Build request headers on demand instead of retaining Authorization."""
        headers = self.headers.copy()
        if self._secret is not None:
            headers["Authorization"] = f"Bearer {self._secret._reveal_for_request()}"
        return headers

    def update_token(self, new_token: str) -> None:
        if not isinstance(new_token, str) or not new_token.strip():
            raise ValueError("ParaTranz token must not be empty")
        env_secret = self._environment_provider.get(self.credential_ref)
        self._secret = env_secret or SecretValue(new_token.strip())
        self._secret_source = "environment" if env_secret is not None else "memory"

    def delete_token(self) -> None:
        """Delete the secure credential; environment overrides are read-only."""
        if self._environment_provider.get(self.credential_ref) is not None:
            raise CredentialStorageError("environment credential is read-only")
        try:
            self._credential_store.delete(self.credential_ref)
        except CredentialStorageError:
            self.credential_capability = SecretStoreCapability(
                False, False, degraded=True, reason_code="SECURE_STORE_DELETE_FAILED"
            )
            raise CredentialStorageError("secure credential delete failed") from None
        self._secret = None
        self._secret_source = "missing"

    def update_timeout(self, new_timeout: int) -> None:
        self.timeout = new_timeout

    def add_header(self, key: str, value: str) -> None:
        if key.casefold() == "authorization":
            raise ValueError("Authorization is managed by the credential boundary")
        self.headers[key] = value

    def remove_header(self, key: str) -> None:
        self.headers.pop(key, None)

    def _persist_secret(self) -> None:
        if self._secret is None:
            return
        if self._secret_source == "environment":
            return
        if not self._credential_store.capability.writable:
            self.credential_capability = SecretStoreCapability(
                False, False, degraded=True, reason_code="SECURE_STORE_NOT_WRITABLE"
            )
            raise CredentialStorageError("secure credential storage is not writable")
        try:
            self._credential_store.set(self.credential_ref, self._secret)
            verified = self._credential_store.get(self.credential_ref)
        except CredentialStorageError:
            self.credential_capability = SecretStoreCapability(
                False, False, degraded=True, reason_code="SECURE_STORE_WRITE_FAILED"
            )
            raise CredentialStorageError("secure credential write failed") from None
        if verified is None or not hmac.compare_digest(
            verified._reveal_for_request(), self._secret._reveal_for_request()
        ):
            self.credential_capability = SecretStoreCapability(
                True, True, degraded=True, reason_code="SECURE_STORE_VERIFY_FAILED"
            )
            raise CredentialStorageError("secure credential verification failed")
        self.credential_capability = self._credential_store.capability

    def save_to_file(self) -> None:
        """Persist non-secret settings only after secure credential verification."""
        self._persist_secret()
        current = self._repository.load().section("paratranz.headers")
        header_updates: dict[str, str | None] = {key: None for key, _value in current.values}
        header_updates.update({key: value for key, value in self.headers.items() if key.casefold() != "authorization"})
        snapshot = self._repository.update_sections({
            "paratranz": {
                "base_url": self.base_url,
                "timeout": self.timeout,
                "credential_ref": self.credential_ref.target_name,
                "user_id": self.user_id,
            },
            "paratranz.headers": header_updates,
        })
        self.config_revision = snapshot.revision

    @classmethod
    def load_from_file(
        cls,
        token: str | None = None,
        *,
        credential_store: CredentialStore | None = None,
        environment: Mapping[str, str] | None = None,
        config_path: str | os.PathLike[str] | None = None,
        repository: ConfigRepository | None = None,
    ) -> ParatranzConfig:
        path = Path(config_path) if config_path is not None else Path(get_config_file_path())
        repo = repository or (
            ConfigRepository(
                path=path,
                legacy_path=path,
                credential_store=credential_store,
            )
            if config_path is not None
            else default_config_repository(credential_store=credential_store)
        )
        path = repo.path
        if not repo.path.exists() and not repo.legacy_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {repo.path}")
        snapshot = repo.load()
        section = snapshot.section("paratranz")
        if not section.values:
            raise ValueError("不是有效的 Paratranz 配置文件")
        reference_raw = section.get("credential_ref", "") or ""
        service, separator, account = reference_raw.rpartition(":")
        reference = CredentialRef(service, account) if separator and service and account else CredentialRef()
        extra_headers = {
            key: value
            for key, value in snapshot.section("paratranz.headers").values
            if key.casefold() not in {"content-type", "authorization"}
        }
        user_id_raw = section.get("user_id")
        config = cls(
            token=token,
            user_id=int(user_id_raw) if user_id_raw and user_id_raw.isdigit() else None,
            base_url=section.get("base_url", cls.DEFAULT_BASE_URL) or cls.DEFAULT_BASE_URL,
            timeout=_parse_timeout(section.get("timeout"), cls.DEFAULT_TIMEOUT),
            extra_headers=extra_headers,
            credential_ref=reference,
            credential_store=credential_store,
            environment=environment,
            config_path=path,
            repository=repo,
        )
        config.config_revision = snapshot.revision
        return config

    @classmethod
    def create_or_load(cls, token: str | None = None, **kwargs: object) -> ParatranzConfig:
        try:
            return cls.load_from_file(token, **kwargs)
        except FileNotFoundError:
            return cls(token=token, **kwargs)


def _parse_timeout(raw: str | None, fallback: int) -> int:
    try:
        return int(raw) if raw is not None else fallback
    except ValueError:
        return fallback
