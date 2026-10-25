"""Download entrypoints require the shared plan and never run a direct merge."""

from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.workbench.cards import download_card as module

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def download_card(monkeypatch):
    entry = TranslationEntry("entry", "entry", "Original", "Local", 1, "INFO:NAM1")
    collection = TranslationEntryCollection((entry,))
    context = SimpleNamespace(collection=collection, slots={"one": object(), "two": object()})
    warnings = []
    calls = []
    monkeypatch.setattr(module.QMessageBox, "warning", lambda _parent, *args: warnings.append(args))
    card = module.DownloadCard(context, lambda **_kwargs: pytest.fail("Direct download worker must not run"))
    yield card, context, calls, warnings
    assert tuple(collection) == (entry,)
    assert entry.translation == "Local" and entry.stage == 1
    card.close()
    card.deleteLater()


@pytest.mark.parametrize("batch", [False, True])
def test_download_button_dispatches_scope_to_shared_plan(download_card, batch):
    card, context, calls, warnings = download_card
    card.bind_operation_plan_facade(SimpleNamespace(begin_download=lambda ctx, *, batch: calls.append((ctx, batch))))

    card.batch_btn.click() if batch else card.btn.click()

    assert calls == [(context, False)]
    assert warnings == []


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("unavailable", ["no_facade", "no_handler", "unsupported"])
@pytest.mark.parametrize("authoritative", [False, True])
def test_unavailable_download_explains_and_preserves_local_content(download_card, batch, unavailable, authoritative):
    card, context, calls, warnings = download_card
    context.uses_authoritative_projection = authoritative
    if unavailable == "no_handler":
        card.bind_operation_plan_facade(SimpleNamespace())
    elif unavailable == "unsupported":
        card.bind_operation_plan_facade(
            SimpleNamespace(
                supports=lambda *_args, **_kwargs: False,
                begin_download=lambda *_args, **_kwargs: pytest.fail("Unsupported plan must not start"),
            )
        )

    card.batch_download() if batch else card.download()

    assert calls == []
    assert len(warnings) == 1
    title, message = warnings[0]
    assert title == "下载入口不可用"
    assert "本次未修改本地内容" in message


def test_legacy_batch_alias_uses_the_same_download_plan_without_direct_fallback(download_card):
    card, context, calls, warnings = download_card
    card.bind_operation_plan_facade(
        SimpleNamespace(
            supports=lambda kind, ctx, *, batch: kind == "download" and ctx is context and not batch,
            begin_download=lambda ctx, *, batch: calls.append((ctx, batch)),
        )
    )

    card.batch_download()
    card.download()

    assert calls == [(context, False), (context, False)]
    assert warnings == []
