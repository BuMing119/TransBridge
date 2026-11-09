# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Legacy import facade for the headless MCP transport."""

from .adapter import MCPAdapter, MCPInvalidParams
from .server import MCPServer

__all__ = ["MCPAdapter", "MCPInvalidParams", "MCPServer"]
