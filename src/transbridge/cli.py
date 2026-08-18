"""Headless-safe TransBridge command-line entry point."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transbridge",
        description="TransBridge translation workflow application",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("gui", "capabilities", "project-context", "mcp"),
        default="gui",
        help="application mode (default: gui)",
    )
    parser.add_argument("--project-id", help="explicit Project context for a headless command")
    parser.add_argument("--pretty", action="store_true", help="pretty-print a headless result")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "gui":
        from .entrypoints.gui import main as gui_main

        return gui_main()
    if args.command == "mcp":
        from .entrypoints.mcp import main as mcp_main

        mcp_args = ["--project-id", args.project_id] if args.project_id else []
        return mcp_main(mcp_args)

    from .entrypoints.cli import run_operation

    result = run_operation(args.command, project_id=args.project_id)
    indent = 2 if args.pretty else None
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=indent))
    return 0 if result.is_success else 2


if __name__ == "__main__":
    raise SystemExit(main())
