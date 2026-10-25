"""The two upload layouts share source names, including empty sources."""

import pytest

from transbridge.application.categorized_upload.classification import source_directory_names
from transbridge.application.categorized_upload.models import UploadSource


def test_plugin_extensions_remain_distinct_in_root_files_and_category_directories():
    sources = (UploadSource("master", "A.esm", ()), UploadSource("plugin", "A.esp", ()))
    assert source_directory_names(sources) == {"master": "A.esm", "plugin": "A.esp"}


def test_empty_and_selected_sources_use_the_same_collision_resolution():
    sources = (UploadSource("one", "A.esm", ()), UploadSource("two", "A.esm", ()))
    names = source_directory_names(sources)
    assert names == source_directory_names(tuple(reversed(sources)))
    assert len(set(names.values())) == 2
    assert all(name.startswith("A.esm_") for name in names.values())


@pytest.mark.parametrize("label", ["../A.esm", "folder/A.esm", "folder\\A.esm", "A:bad.esm"])
def test_source_names_are_single_virtual_path_components(label):
    name = source_directory_names((UploadSource("source", label, ()),))["source"]
    assert "/" not in name and "\\" not in name and ":" not in name
    assert not name.startswith(".")
