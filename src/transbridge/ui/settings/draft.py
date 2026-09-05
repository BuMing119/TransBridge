"""Qt-free detached settings draft and guarded persistence coordination."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import os
from urllib.parse import urlsplit


@dataclass(slots=True)
class ParaTranzSettingsDraft:
    base_url: str
    timeout: int
    user_id: int | None
    replacement_token: str = ""
    disconnect_requested: bool = False


@dataclass(frozen=True, slots=True)
class SettingsSaveResult:
    saved: bool
    message: str = ""


class SettingsConfigDraft:
    """Keep service edits detached until the settings dialog is applied."""

    def __init__(
        self,
        llm_config: object | None = None,
        paratranz_config: object | None = None,
        *,
        reload_llm: Callable[[], object] | None = None,
        on_paratranz_saved: Callable[[object], None] | None = None,
    ) -> None:
        self._llm_source = llm_config
        self._paratranz_source = paratranz_config
        self._reload_llm = reload_llm
        self._on_paratranz_saved = on_paratranz_saved
        self.llm = _detached_copy(llm_config)
        self.llm_revision = int(getattr(llm_config, "config_revision", 0) or 0)
        self.paratranz = (
            None
            if paratranz_config is None
            else ParaTranzSettingsDraft(
                base_url=str(getattr(paratranz_config, "base_url", "") or ""),
                timeout=int(getattr(paratranz_config, "timeout", 30) or 30),
                user_id=getattr(paratranz_config, "user_id", None),
            )
        )

    @property
    def has_llm(self) -> bool:
        return self.llm is not None

    @property
    def has_paratranz(self) -> bool:
        return self.paratranz is not None

    @property
    def llm_secret_read_only(self) -> bool:
        return _environment_has(self._llm_source, "TRANSBRIDGE_LLM_API_KEY")

    @property
    def embedding_secret_read_only(self) -> bool:
        return _environment_has(self._llm_source, "TRANSBRIDGE_EMBEDDING_API_KEY")

    @property
    def mcp_secret_read_only(self) -> bool:
        return _environment_has(self._llm_source, "TRANSBRIDGE_MCP_AUTH_TOKEN")

    def validate(self) -> str | None:
        if self.llm is not None:
            base_url = str(getattr(self.llm, "base_url", "") or "").strip()
            if base_url and not _is_http_url(base_url):
                return "AI 服务 Base URL 必须是有效的 HTTP(S) 地址。"
            embedding = getattr(self.llm, "embedding", None)
            embedding_url = str(getattr(embedding, "base_url", "") or "").strip()
            if embedding_url and not _is_http_url(embedding_url):
                return "Embedding Base URL 必须是有效的 HTTP(S) 地址。"
            for attr, label in (("excel_original_col", "原文列"), ("excel_translation_col", "译文列")):
                value = str(getattr(self.llm, attr, "") or "").strip()
                if not value.isascii() or not value.isalpha():
                    return f"Excel {label}必须使用字母列名。"
        if self.paratranz is not None:
            if not _is_paratranz_url(self.paratranz.base_url.strip()):
                return "ParaTranz Base URL 必须是有效的 HTTP(S) 地址。"
            if not 5 <= int(self.paratranz.timeout) <= 300:
                return "ParaTranz 请求超时必须在 5 到 300 秒之间。"
        return None

    def save(self) -> SettingsSaveResult:
        validation = self.validate()
        if validation:
            return SettingsSaveResult(False, validation)
        if self.llm is not None and self._reload_llm is not None and self.llm_revision:
            try:
                latest = self._reload_llm()
            except Exception:
                return SettingsSaveResult(False, "无法确认 AI 设置版本，请重新打开设置后再试。")
            latest_revision = int(getattr(latest, "config_revision", 0) or 0)
            if latest_revision != self.llm_revision:
                return SettingsSaveResult(False, "AI 设置已在其他窗口中更改，请重新打开设置后再试。")
        try:
            if self.llm is not None:
                self.llm.save_to_file()
            if self.paratranz is not None and self._paratranz_source is not None:
                self._save_paratranz()
        except Exception:
            return SettingsSaveResult(False, "无法保存设置；更改尚未完整应用，请检查凭据存储后重试。")
        return SettingsSaveResult(True)

    def _save_paratranz(self) -> None:
        draft = self.paratranz
        source = self._paratranz_source
        assert draft is not None and source is not None
        previous = (
            getattr(source, "base_url", ""),
            getattr(source, "timeout", 30),
            getattr(source, "user_id", None),
            getattr(source, "_secret", None),
            getattr(source, "_secret_source", "missing"),
            getattr(source, "credential_capability", None),
        )
        try:
            source.base_url = draft.base_url.strip()
            source.update_timeout(int(draft.timeout))
            source.user_id = draft.user_id
            if draft.replacement_token and not draft.disconnect_requested:
                source.update_token(draft.replacement_token)
                source.user_id = None
            source.save_to_file()
            if draft.disconnect_requested:
                source.delete_token()
        except Exception:
            (
                source.base_url,
                source.timeout,
                source.user_id,
                source._secret,
                source._secret_source,
                source.credential_capability,
            ) = previous
            raise
        if self._on_paratranz_saved is not None:
            self._on_paratranz_saved(source)


def _detached_copy(config: object | None) -> object | None:
    if config is None:
        return None
    copy_for_execution = getattr(config, "copy_for_execution", None)
    if callable(copy_for_execution):
        copied = copy_for_execution()
        # Execution copies intentionally drop persistence providers.  A settings
        # draft remains detached from values but must commit through the same
        # repository, credential store and environment boundary it was loaded from.
        for attr in ("_repository", "_credential_store", "_environment"):
            if hasattr(config, attr) and hasattr(copied, attr):
                setattr(copied, attr, getattr(config, attr))
        return copied
    return deepcopy(config)


def _environment_has(source: object | None, variable: str) -> bool:
    environment = getattr(source, "_environment", None)
    if environment is None:
        environment = os.environ
    return bool(str(environment.get(variable, "") or "").strip())


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not parsed.username


def _is_paratranz_url(value: str) -> bool:
    if not _is_http_url(value):
        return False
    parsed = urlsplit(value)
    return not parsed.query and not parsed.fragment


__all__ = ["ParaTranzSettingsDraft", "SettingsConfigDraft", "SettingsSaveResult"]
