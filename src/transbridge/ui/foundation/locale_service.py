"""Qt-free locale loading, fallback, and preference contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import gettext as gettext_module
from pathlib import Path
import re
from typing import Protocol, runtime_checkable

from transbridge.ui.i18n import CATALOG_DOMAIN, CATALOG_SCHEMA_VERSION, SOURCE_LOCALE, catalog_root

_LOCALE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


class LocaleDiagnosticCode(StrEnum):
    PREFERENCE_LOAD_FAILED = "locale_preference_load_failed"
    LOCALE_INVALID = "locale_invalid"
    CATALOG_MISSING = "locale_catalog_missing"
    CATALOG_INVALID = "locale_catalog_invalid"
    CATALOG_SCHEMA_UNSUPPORTED = "locale_catalog_schema_unsupported"
    PREFERENCE_WRITE_FAILED = "locale_preference_write_failed"
    MSGID_MISSING = "locale_msgid_missing"


@dataclass(frozen=True, slots=True)
class LocalePreference:
    locale_id: str


@dataclass(frozen=True, slots=True)
class LocaleSnapshot:
    source_locale: str
    active_locale: str
    catalog_version: str
    fallback: bool
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LocaleChangeResult:
    requested_locale: str
    accepted: bool
    persisted: bool
    restart_required: bool
    diagnostic_code: str | None = None
    message: str = ""

    def __post_init__(self) -> None:
        if self.accepted and self.diagnostic_code is not None:
            raise ValueError("accepted locale change cannot contain a failure diagnostic")
        if not self.accepted and not self.diagnostic_code:
            raise ValueError("rejected locale change requires a diagnostic code")


@dataclass(frozen=True, slots=True)
class LocaleLookupDiagnostic:
    code: str
    locale_id: str
    msgid: str
    catalog_version: str
    count: int


@runtime_checkable
class TranslationCatalog(Protocol):
    locale_id: str
    schema_version: int
    catalog_version: str

    def gettext(self, msgid: str) -> str: ...

    def ngettext(self, singular: str, plural: str, n: int) -> str: ...


@runtime_checkable
class CatalogLoader(Protocol):
    def load(self, locale_id: str) -> TranslationCatalog: ...


@runtime_checkable
class UiPreferenceRepositoryPort(Protocol):
    def load(self) -> object: ...

    def save_locale(self, locale: str) -> object: ...


class LocaleCatalogError(RuntimeError):
    def __init__(self, code: LocaleDiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _SourceCatalog:
    locale_id: str
    schema_version: int = CATALOG_SCHEMA_VERSION
    catalog_version: str = "source-1"

    @staticmethod
    def gettext(msgid: str) -> str:
        return msgid

    @staticmethod
    def ngettext(singular: str, plural: str, n: int) -> str:
        return singular if n == 1 else plural


@dataclass(frozen=True, slots=True)
class GettextCatalog:
    """Validated wrapper around a standard GNU gettext catalog."""

    locale_id: str
    schema_version: int
    catalog_version: str
    translations: gettext_module.NullTranslations

    def gettext(self, msgid: str) -> str:
        return self.translations.gettext(msgid)

    def ngettext(self, singular: str, plural: str, n: int) -> str:
        return self.translations.ngettext(singular, plural, n)


class GettextCatalogLoader:
    """Load versioned gettext catalogs from an injected resource directory."""

    def __init__(self, root: Path | None = None, *, domain: str = CATALOG_DOMAIN) -> None:
        self._root = Path(root) if root is not None else catalog_root()
        self._domain = domain

    def load(self, locale_id: str) -> GettextCatalog:
        try:
            translations = gettext_module.translation(
                self._domain,
                localedir=str(self._root),
                languages=[locale_id],
                fallback=False,
            )
        except FileNotFoundError as exc:
            raise LocaleCatalogError(
                LocaleDiagnosticCode.CATALOG_MISSING,
                f"catalog is unavailable for locale {locale_id}",
            ) from exc
        except Exception as exc:
            raise LocaleCatalogError(
                LocaleDiagnosticCode.CATALOG_INVALID,
                f"catalog could not be loaded for locale {locale_id}",
            ) from exc

        metadata = {str(key).casefold(): str(value) for key, value in translations.info().items()}
        try:
            schema_version = int(metadata["x-transbridge-catalog-schema"])
            catalog_version = metadata["x-transbridge-catalog-version"].strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise LocaleCatalogError(
                LocaleDiagnosticCode.CATALOG_INVALID,
                f"catalog metadata is incomplete for locale {locale_id}",
            ) from exc
        if not catalog_version:
            raise LocaleCatalogError(
                LocaleDiagnosticCode.CATALOG_INVALID,
                f"catalog version is empty for locale {locale_id}",
            )
        return GettextCatalog(locale_id, schema_version, catalog_version, translations)


class LocaleService:
    """Own one immutable startup catalog and persist restart-bound preferences."""

    def __init__(
        self,
        preferences: UiPreferenceRepositoryPort,
        loader: CatalogLoader | None = None,
        *,
        source_locale: str = SOURCE_LOCALE,
    ) -> None:
        self._preferences = preferences
        self._loader = loader or GettextCatalogLoader()
        self._source_locale = _normalize_locale(source_locale)
        self._catalog: TranslationCatalog | None = None
        self._snapshot: LocaleSnapshot | None = None
        self._lookup_counts: dict[tuple[str, str, str], int] = {}
        self._closed = False

    @property
    def snapshot(self) -> LocaleSnapshot:
        if self._snapshot is None:
            raise RuntimeError("LocaleService has not been started")
        return self._snapshot

    def start(self) -> LocaleSnapshot:
        if self._closed:
            raise RuntimeError("LocaleService is closed")
        if self._snapshot is not None:
            return self._snapshot

        diagnostics: list[str] = []
        requested = self._source_locale
        try:
            preference_snapshot = self._preferences.load()
            requested = str(getattr(preference_snapshot, "locale", self._source_locale))
            diagnostics.extend(
                str(code)
                for code in getattr(preference_snapshot, "diagnostics", ())
                if str(code).startswith("ui_locale")
            )
        except Exception:
            diagnostics.append(LocaleDiagnosticCode.PREFERENCE_LOAD_FAILED.value)

        try:
            requested = _normalize_locale(requested)
            catalog = self._load_validated(requested)
        except LocaleCatalogError as exc:
            diagnostics.append(exc.code.value)
            catalog = _SourceCatalog(self._source_locale)

        fallback = bool(diagnostics) or catalog.locale_id != requested
        self._catalog = catalog
        self._snapshot = LocaleSnapshot(
            source_locale=self._source_locale,
            active_locale=catalog.locale_id,
            catalog_version=catalog.catalog_version,
            fallback=fallback,
            diagnostics=tuple(dict.fromkeys(diagnostics)),
        )
        return self._snapshot

    def gettext(self, msgid: str) -> str:
        if not isinstance(msgid, str) or not msgid:
            raise ValueError("msgid must be a non-empty string")
        catalog = self._active_catalog()
        translated = catalog.gettext(msgid)
        if not translated:
            translated = msgid
        if catalog.locale_id != self._source_locale and translated == msgid:
            self._record_missing(msgid)
        return translated

    def ngettext(self, singular: str, plural: str, n: int) -> str:
        if not singular or not plural:
            raise ValueError("singular and plural msgids must be non-empty")
        catalog = self._active_catalog()
        translated = catalog.ngettext(singular, plural, n)
        fallback = singular if n == 1 else plural
        if not translated:
            translated = fallback
        if catalog.locale_id != self._source_locale and translated == fallback:
            self._record_missing(singular)
        return translated

    def set_preference(self, locale_id: str, *, persist: bool = True) -> LocaleChangeResult:
        if self._closed:
            return LocaleChangeResult(
                locale_id,
                accepted=False,
                persisted=False,
                restart_required=False,
                diagnostic_code="locale_service_closed",
            )
        try:
            normalized = _normalize_locale(locale_id)
            self._load_validated(normalized)
        except LocaleCatalogError as exc:
            return LocaleChangeResult(
                locale_id,
                accepted=False,
                persisted=False,
                restart_required=False,
                diagnostic_code=exc.code.value,
                message=str(exc),
            )

        if persist:
            try:
                result = self._preferences.save_locale(normalized)
            except Exception as exc:
                return LocaleChangeResult(
                    normalized,
                    accepted=False,
                    persisted=False,
                    restart_required=False,
                    diagnostic_code=LocaleDiagnosticCode.PREFERENCE_WRITE_FAILED.value,
                    message=str(exc),
                )
            if not bool(getattr(result, "saved", False)):
                return LocaleChangeResult(
                    normalized,
                    accepted=False,
                    persisted=False,
                    restart_required=False,
                    diagnostic_code=str(
                        getattr(result, "diagnostic_code", LocaleDiagnosticCode.PREFERENCE_WRITE_FAILED.value)
                    ),
                    message=str(getattr(result, "message", "")),
                )

        current = self.snapshot.active_locale if self._snapshot is not None else self._source_locale
        return LocaleChangeResult(
            normalized,
            accepted=True,
            persisted=persist,
            restart_required=normalized != current,
        )

    def lookup_diagnostics(self) -> tuple[LocaleLookupDiagnostic, ...]:
        return tuple(
            LocaleLookupDiagnostic(
                LocaleDiagnosticCode.MSGID_MISSING.value,
                locale_id,
                msgid,
                catalog_version,
                count,
            )
            for (locale_id, msgid, catalog_version), count in sorted(self._lookup_counts.items())
        )

    def close(self) -> None:
        self._catalog = None
        self._snapshot = None
        self._lookup_counts.clear()
        self._closed = True

    def _load_validated(self, locale_id: str) -> TranslationCatalog:
        if locale_id == self._source_locale:
            return _SourceCatalog(self._source_locale)
        try:
            catalog = self._loader.load(locale_id)
        except LocaleCatalogError:
            raise
        except FileNotFoundError as exc:
            raise LocaleCatalogError(
                LocaleDiagnosticCode.CATALOG_MISSING,
                f"catalog is unavailable for locale {locale_id}",
            ) from exc
        except Exception as exc:
            raise LocaleCatalogError(
                LocaleDiagnosticCode.CATALOG_INVALID,
                f"catalog could not be loaded for locale {locale_id}",
            ) from exc
        if catalog.locale_id != locale_id or not catalog.catalog_version:
            raise LocaleCatalogError(
                LocaleDiagnosticCode.CATALOG_INVALID,
                f"catalog identity is invalid for locale {locale_id}",
            )
        if catalog.schema_version != CATALOG_SCHEMA_VERSION:
            raise LocaleCatalogError(
                LocaleDiagnosticCode.CATALOG_SCHEMA_UNSUPPORTED,
                f"catalog schema {catalog.schema_version} is unsupported",
            )
        return catalog

    def _active_catalog(self) -> TranslationCatalog:
        if self._closed:
            raise RuntimeError("LocaleService is closed")
        if self._catalog is None:
            raise RuntimeError("LocaleService has not been started")
        return self._catalog

    def _record_missing(self, msgid: str) -> None:
        snapshot = self.snapshot
        key = (snapshot.active_locale, msgid, snapshot.catalog_version)
        self._lookup_counts[key] = self._lookup_counts.get(key, 0) + 1


def _normalize_locale(locale_id: str) -> str:
    if not isinstance(locale_id, str):
        raise LocaleCatalogError(LocaleDiagnosticCode.LOCALE_INVALID, "locale ID must be a string")
    normalized = locale_id.strip()
    if not _LOCALE_PATTERN.fullmatch(normalized):
        raise LocaleCatalogError(LocaleDiagnosticCode.LOCALE_INVALID, "locale ID is invalid")
    return normalized


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "CatalogLoader",
    "GettextCatalog",
    "GettextCatalogLoader",
    "LocaleCatalogError",
    "LocaleChangeResult",
    "LocaleDiagnosticCode",
    "LocaleLookupDiagnostic",
    "LocalePreference",
    "LocaleService",
    "LocaleSnapshot",
    "TranslationCatalog",
    "UiPreferenceRepositoryPort",
]
