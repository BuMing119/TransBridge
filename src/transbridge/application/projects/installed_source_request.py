"""Read an installed source by identity without requiring an import filename."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InstalledSourceRequest:
    location: str
    format_hint: object
    expected_fingerprint: str | None
    options: tuple = ()

    def fingerprint_data(self):
        return {
            "format_hint": self.format_hint.value,
            "expected_fingerprint": self.expected_fingerprint,
            "options": dict(self.options),
        }
