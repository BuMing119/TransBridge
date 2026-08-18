"""Compatibility executable used by the PyInstaller specification."""

from transbridge.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
