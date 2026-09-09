"""An MCP server for the Sleeper fantasy football API."""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__", "main"]


def main() -> None:
    """Console-script entry point; imported lazily to keep startup cheap."""
    from .server import main as _main

    _main()
