"""Independent, headless MCP stdio process entry point."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import logging
import sys

from transbridge import __version__
from transbridge.application.security import SecretRedactor


class RedactingLogFilter(logging.Filter):
    """Fail closed when third-party log records contain a known secret shape."""

    def __init__(self) -> None:
        super().__init__()
        self._redactor = SecretRedactor.default()

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redactor.redact_text(str(record.getMessage()))
        record.args = ()
        return True


def configure_logging(level: str = "WARNING") -> None:
    """Configure diagnostics on stderr only; stdout is reserved for JSON-RPC."""

    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RedactingLogFilter())
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.WARNING),
        handlers=(handler,),
        force=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="transbridge-mcp", description="TransBridge MCP stdio server")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--project-id", help="explicit Project context for this process")
    parser.add_argument(
        "--authorized-root",
        action="append",
        default=None,
        help="filesystem root authorized for this process (repeatable)",
    )
    parser.add_argument("--log-level", default="WARNING")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)

    from transbridge.entrypoints.headless import build_headless_binding
    from transbridge.smart_assistant.mcp import MCPAdapter, MCPServer

    roots: tuple[str, ...] | None = None
    if args.authorized_root is not None:
        roots = tuple(str(value) for value in args.authorized_root)
    binding = build_headless_binding(
        "mcp",
        project_id=args.project_id,
        authorized_roots=roots,
    )
    adapter = MCPAdapter(binding=binding)
    server = MCPServer(adapter=adapter, on_close=binding.runtime.close)
    server.run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
