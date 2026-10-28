"""Resolve category paths for current batch download sources."""

from pathlib import PurePosixPath

from transbridge.application.io.paratranz_classification import classify_entries

from .download_paths import validate_download_path


def current_category_paths(root_path, entries):
    """Use the upload classifier against the complete source, before selection."""
    validate_download_path(root_path)
    directory = str(PurePosixPath(root_path).with_suffix(""))
    return tuple(f"{directory}/{group.path}" for group in classify_entries(tuple(entries)))
