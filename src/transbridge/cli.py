"""Headless-safe TransBridge command-line entry point."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys

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
    parser.add_argument("--open-project", help="Project file to open explicitly in GUI mode")
    parser.add_argument("--import-project", help=".transbridge archive to review and import in GUI mode")
    parser.add_argument("--pretty", action="store_true", help="pretty-print a headless result")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if len(raw_args) == 1 and Path(raw_args[0]).suffix.casefold() == ".transbridge":
        # Windows file associations conventionally invoke the executable with
        # the selected document as a lone positional argument.
        raw_args = ["gui", "--import-project", raw_args[0]]
    args = build_parser().parse_args(raw_args)
    if args.open_project and args.command != "gui":
        build_parser().error("--open-project is only valid with the gui command")
    if args.import_project and args.command != "gui":
        build_parser().error("--import-project is only valid with the gui command")
    if args.open_project and args.import_project:
        build_parser().error("--open-project and --import-project cannot be used together")
    if args.command == "gui":
        from .entrypoints.gui import main as gui_main

        kwargs = {"initial_project_path": args.open_project}
        if args.import_project:
            kwargs["initial_import_path"] = args.import_project
        return gui_main(**kwargs)
    if args.command == "mcp":
        from .entrypoints.mcp import main as mcp_main

        mcp_args = ["--project-id", args.project_id] if args.project_id else []
        return mcp_main(mcp_args)

    from .entrypoints.cli import run_operation

    result = run_operation(args.command, project_id=args.project_id)
    indent = 2 if args.pretty else None
    # ASCII JSON remains UTF-8 compatible even when Windows stdout uses a legacy code page.
    print(json.dumps(result.to_dict(), ensure_ascii=True, indent=indent))
    return 0 if result.is_success else 2


if __name__ == "__main__":
    raise SystemExit(main())
