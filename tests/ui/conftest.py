"""Keep optional model consent and its persistence isolated from real user preferences."""

import pytest


class _AlignmentSettings:
    def __init__(self):
        self.values = {"remember": True, "enabled": False}

    def value(self, key, default=None, *, type=None):
        value = self.values.get(key, default)
        return value if type is None else type(value)

    def setValue(self, key, value):  # noqa: N802 - QSettings API
        self.values[key] = value

    def remove(self, key):
        self.values.pop(key, None)

    def sync(self):
        pass


@pytest.fixture(autouse=True)
def isolated_alignment_consent(monkeypatch):
    from PyQt6.QtWidgets import QApplication

    from transbridge.ui.workbench import alignment_consent

    app = QApplication.instance() or QApplication([])
    service = alignment_consent.AlignmentConsent(app, settings=_AlignmentSettings())
    monkeypatch.setattr(alignment_consent, "get_alignment_consent", lambda: service)
    monkeypatch.setattr(app, alignment_consent._APP_ATTRIBUTE, service, raising=False)
    yield service
    service.deleteLater()
