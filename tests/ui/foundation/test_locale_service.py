from __future__ import annotations

from dataclasses import dataclass
import re
from types import SimpleNamespace

import pytest

from transbridge.ui.foundation.locale_service import (
    LocaleDiagnosticCode,
    LocaleService,
)
from transbridge.ui.i18n import catalog_root, source_template
from transbridge.ui.shell.action_catalog import DEFAULT_ACTION_CATALOG


@dataclass(frozen=True)
class _Catalog:
    locale_id: str
    messages: dict[str, str]
    plurals: dict[tuple[str, str], tuple[str, str]]
    schema_version: int = 1
    catalog_version: str = "test-1"

    def gettext(self, msgid: str) -> str:
        return self.messages.get(msgid, msgid)

    def ngettext(self, singular: str, plural: str, n: int) -> str:
        translated = self.plurals.get((singular, plural))
        if translated is None:
            return singular if n == 1 else plural
        return translated[0] if n == 1 else translated[1]


class _Loader:
    def __init__(self, catalogs: dict[str, object]) -> None:
        self.catalogs = catalogs
        self.calls: list[str] = []

    def load(self, locale_id: str):
        self.calls.append(locale_id)
        value = self.catalogs[locale_id]
        if isinstance(value, Exception):
            raise value
        return value


class _Preferences:
    def __init__(self, locale: str = "zh-CN", *, saved: bool = True) -> None:
        self.locale = locale
        self.saved = saved
        self.saved_locales: list[str] = []

    def load(self):
        return SimpleNamespace(locale=self.locale, diagnostics=())

    def save_locale(self, locale: str):
        self.saved_locales.append(locale)
        return SimpleNamespace(
            saved=self.saved,
            diagnostic_code=None if self.saved else "ui_locale_write_failed",
            message="injected write failure" if not self.saved else "",
        )


def _english_catalog(*, schema_version: int = 1) -> _Catalog:
    return _Catalog(
        "en-US",
        {"设置": "Settings"},
        {("{n} 个任务", "{n} 个任务们"): ("{n} task", "{n} tasks")},
        schema_version=schema_version,
    )


def test_source_locale_uses_msgids_without_loading_a_catalog() -> None:
    loader = _Loader({})
    service = LocaleService(_Preferences(), loader)

    snapshot = service.start()

    assert snapshot.active_locale == "zh-CN"
    assert not snapshot.fallback
    assert service.gettext("设置") == "设置"
    assert service.ngettext("一项", "多项", 2) == "多项"
    assert loader.calls == []


def test_catalog_loads_once_and_missing_msgids_are_aggregated() -> None:
    loader = _Loader({"en-US": _english_catalog()})
    service = LocaleService(_Preferences("en-US"), loader)
    service.start()

    assert service.gettext("设置") == "Settings"
    assert service.ngettext("{n} 个任务", "{n} 个任务们", 2) == "{n} tasks"
    assert service.gettext("未翻译") == "未翻译"
    assert service.gettext("未翻译") == "未翻译"

    assert loader.calls == ["en-US"]
    assert service.lookup_diagnostics()[0].msgid == "未翻译"
    assert service.lookup_diagnostics()[0].count == 2


@pytest.mark.parametrize(
    ("catalog", "code"),
    [
        (FileNotFoundError("missing"), LocaleDiagnosticCode.CATALOG_MISSING.value),
        (ValueError("corrupt"), LocaleDiagnosticCode.CATALOG_INVALID.value),
        (_english_catalog(schema_version=2), LocaleDiagnosticCode.CATALOG_SCHEMA_UNSUPPORTED.value),
    ],
)
def test_invalid_or_forward_catalog_falls_back_to_source(catalog: object, code: str) -> None:
    service = LocaleService(_Preferences("en-US"), _Loader({"en-US": catalog}))

    snapshot = service.start()

    assert snapshot.active_locale == "zh-CN"
    assert snapshot.fallback
    assert code in snapshot.diagnostics
    assert service.gettext("设置") == "设置"


def test_preference_change_validates_and_persists_but_keeps_startup_catalog() -> None:
    preferences = _Preferences()
    loader = _Loader({"en-US": _english_catalog()})
    service = LocaleService(preferences, loader)
    service.start()

    result = service.set_preference("en-US")

    assert result.accepted and result.persisted and result.restart_required
    assert preferences.saved_locales == ["en-US"]
    assert service.snapshot.active_locale == "zh-CN"
    assert service.gettext("设置") == "设置"


def test_invalid_locale_and_write_failure_do_not_claim_restart() -> None:
    loader = _Loader({"en-US": _english_catalog()})
    service = LocaleService(_Preferences(saved=False), loader)
    service.start()

    traversal = service.set_preference("../../en-US")
    failed_write = service.set_preference("en-US")

    assert not traversal.accepted
    assert traversal.diagnostic_code == LocaleDiagnosticCode.LOCALE_INVALID.value
    assert not failed_write.accepted
    assert failed_write.diagnostic_code == "ui_locale_write_failed"
    assert not failed_write.restart_required


def test_close_releases_catalog_and_prevents_late_lookup() -> None:
    service = LocaleService(_Preferences(), _Loader({}))
    service.start()
    service.close()

    with pytest.raises(RuntimeError, match="closed"):
        service.gettext("设置")


def test_source_resources_are_discoverable_without_install_path_assumptions() -> None:
    assert source_template().is_file()
    assert (catalog_root() / "zh-CN" / "LC_MESSAGES" / "transbridge.po").is_file()


def test_source_template_covers_menu_catalog_and_critical_settings_msgids() -> None:
    template = source_template().read_text(encoding="utf-8")
    msgids = set(re.findall(r'^msgid "(.*)"$', template, flags=re.MULTILINE))
    menu_msgids = {"文件", "项目", "翻译版本", "翻译", "同步与发布", "视图", "设置", "帮助"}
    menu_msgids.update(item.label for item in DEFAULT_ACTION_CATALOG.all())
    menu_msgids.update(item.status_tip for item in DEFAULT_ACTION_CATALOG.all() if item.status_tip)
    settings_msgids = {
        "通用设置",
        "主题模式",
        "主题提供者",
        "当前生效配色",
        "跟随系统",
        "浅色",
        "深色",
        "应用",
        "恢复默认",
        "取消",
        "所选主题当前不可用，请选择其他主题。",
        "无法应用所选主题；当前主题保持不变。",
    }
    assert menu_msgids | settings_msgids <= msgids
