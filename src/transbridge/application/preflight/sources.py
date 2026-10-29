"""Optional source references, separate from captured project content."""

from pathlib import Path, PureWindowsPath


def existing_reference(location: str | None) -> str:
    """Only inspect existence; an unavailable reference never blocks a project.

    Permission and device errors do not prove absence. Keep those references
    without turning an optional hint into an access or content preflight.
    """
    if not location:
        return ""
    try:
        Path(location).stat()
    except (FileNotFoundError, NotADirectoryError):
        return ""
    except (OSError, ValueError):
        return location
    return location


def source_reference(source):
    """Prepare reference cleanup without mutating the registered source."""
    result = dict(source)
    old = str(source.get("location") or "")
    result["location"] = existing_reference(old)
    if old and not result.get("display_name"):
        result["display_name"] = PureWindowsPath(old).name or str(source["source_id"])
    if result["location"] != old and isinstance(source.get("legacy"), dict):
        legacy = dict(source["legacy"])
        if "path" in legacy:
            legacy["path"] = result["location"]
        result["legacy"] = legacy
    return result
