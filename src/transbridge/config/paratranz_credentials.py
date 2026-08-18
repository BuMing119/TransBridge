"""ParaTranz credential capabilities and Windows secure-store adapter.

The configuration file stores only :class:`CredentialRef`; secret material is
kept in the process for as little time as the legacy facade permits and is
never included in object representations or diagnostics.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from typing import Any, Protocol

from transbridge.application.security import SecretRedactor


class CredentialStorageError(RuntimeError):
    """A secure credential operation failed without exposing secret material."""


@dataclass(frozen=True, slots=True)
class CredentialRef:
    service: str = "TransBridge.ParaTranz"
    account: str = "default"

    def __post_init__(self) -> None:
        if not self.service.strip() or not self.account.strip():
            raise ValueError("credential reference fields must not be empty")

    @property
    def target_name(self) -> str:
        return f"{self.service}:{self.account}"


@dataclass(frozen=True, slots=True)
class SecretStoreCapability:
    available: bool
    writable: bool
    degraded: bool = False
    reason_code: str | None = None


class SecretValue:
    """Opaque secret value whose normal string projections are fail-closed."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("secret value must be a non-empty string")
        self.__value = value

    def __repr__(self) -> str:
        return "SecretValue(***)"

    def __str__(self) -> str:
        return SecretRedactor.REDACTED

    def _reveal_for_request(self) -> str:
        """Return plaintext only to ParaTranz request/config compatibility code."""

        return self.__value


class CredentialStore(Protocol):
    @property
    def capability(self) -> SecretStoreCapability: ...

    def get(self, reference: CredentialRef) -> SecretValue | None: ...

    def set(self, reference: CredentialRef, value: SecretValue) -> None: ...

    def delete(self, reference: CredentialRef) -> None: ...


class EnvironmentCredentialProvider:
    """Read-only, headless credential override."""

    def __init__(
        self,
        environment: Mapping[str, str] | None = None,
        variable: str = "TRANSBRIDGE_PARATRANZ_TOKEN",
    ) -> None:
        self._environment = os.environ if environment is None else environment
        self.variable = variable

    @property
    def capability(self) -> SecretStoreCapability:
        return SecretStoreCapability(available=self.get(CredentialRef()) is not None, writable=False)

    def get(self, reference: CredentialRef) -> SecretValue | None:
        del reference
        value = self._environment.get(self.variable, "").strip()
        return SecretValue(value) if value else None


class UnavailableCredentialStore:
    """Fail-closed store used when the platform has no approved secure backend."""

    @property
    def capability(self) -> SecretStoreCapability:
        return SecretStoreCapability(False, False, reason_code="SECURE_STORE_UNAVAILABLE")

    def get(self, reference: CredentialRef) -> SecretValue | None:
        del reference
        return None

    def set(self, reference: CredentialRef, value: SecretValue) -> None:
        del reference, value
        raise CredentialStorageError("secure credential storage is unavailable")

    def delete(self, reference: CredentialRef) -> None:
        del reference
        raise CredentialStorageError("secure credential storage is unavailable")


class WindowsCredentialStore:
    """Windows Credential Manager adapter implemented with the platform API."""

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self) -> None:
        self._available = os.name == "nt"

    @property
    def capability(self) -> SecretStoreCapability:
        return SecretStoreCapability(self._available, self._available)

    @staticmethod
    def _api():
        if os.name != "nt":
            raise CredentialStorageError("Windows Credential Manager is unavailable")
        import ctypes

        return ctypes, ctypes.WinDLL("Advapi32.dll", use_last_error=True)

    def get(self, reference: CredentialRef) -> SecretValue | None:
        if not self._available:
            return None
        ctypes, api = self._api()
        from ctypes import wintypes

        class Credential(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        pointer = ctypes.POINTER(Credential)()
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        api.CredReadW.restype = wintypes.BOOL
        if not api.CredReadW(reference.target_name, self._CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:  # ERROR_NOT_FOUND
                return None
            raise CredentialStorageError("secure credential read failed")
        try:
            record = pointer.contents
            raw = ctypes.string_at(record.CredentialBlob, record.CredentialBlobSize)
            return SecretValue(raw.decode("utf-16-le")) if raw else None
        except (UnicodeDecodeError, ValueError) as exc:
            raise CredentialStorageError("secure credential data is invalid") from exc
        finally:
            api.CredFree(pointer)

    def set(self, reference: CredentialRef, value: SecretValue) -> None:
        ctypes, api = self._api()
        from ctypes import wintypes

        class Credential(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        raw = value._reveal_for_request().encode("utf-16-le")
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = Credential(
            0,
            self._CRED_TYPE_GENERIC,
            reference.target_name,
            None,
            wintypes.FILETIME(),
            len(raw),
            ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
            self._CRED_PERSIST_LOCAL_MACHINE,
            0,
            None,
            None,
            reference.account,
        )
        api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        if not api.CredWriteW(ctypes.byref(credential), 0):
            raise CredentialStorageError("secure credential write failed")

    def delete(self, reference: CredentialRef) -> None:
        ctypes, api = self._api()
        from ctypes import wintypes

        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        if not api.CredDeleteW(reference.target_name, self._CRED_TYPE_GENERIC, 0):
            if ctypes.get_last_error() != 1168:
                raise CredentialStorageError("secure credential delete failed")


def default_credential_store() -> CredentialStore:
    return WindowsCredentialStore() if os.name == "nt" else UnavailableCredentialStore()


def redact_credential_data(value: Any, *secrets: SecretValue | None) -> Any:
    """Apply shared structural redaction plus exact known-secret canaries."""

    redacted = SecretRedactor.default().redact(value)
    literals = tuple(secret._reveal_for_request() for secret in secrets if secret is not None)

    def exact(item: Any) -> Any:
        if isinstance(item, str):
            for literal in literals:
                item = item.replace(literal, SecretRedactor.REDACTED)
            return item
        if isinstance(item, dict):
            return {exact(key): exact(child) for key, child in item.items()}
        if isinstance(item, list):
            return [exact(child) for child in item]
        if isinstance(item, tuple):
            return tuple(exact(child) for child in item)
        return item

    return exact(redacted)
