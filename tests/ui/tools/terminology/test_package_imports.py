"""Fresh interpreters exercise both public UI entry-point import orders."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("first", ["terminology", "terminology_profiles"])
def test_profile_and_terminology_packages_import_independently(first):
    command = (
        f"import transbridge.ui.tools.{first}; "
        "from transbridge.ui.tools.terminology import TerminologyLauncher, TerminologyWindow; "
        "from transbridge.ui.tools.terminology_profiles import TerminologySourceImportController; "
        "assert TerminologyLauncher and TerminologyWindow and TerminologySourceImportController"
    )
    result = subprocess.run([sys.executable, "-c", command], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
