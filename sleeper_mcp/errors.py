"""Exception hierarchy for the Sleeper MCP server."""

from __future__ import annotations


class SleeperError(Exception):
    """Base class for every error raised by this package."""


class SleeperNotFound(SleeperError):
    """The requested resource does not exist (HTTP 404, or a null body)."""


class SleeperRateLimited(SleeperError):
    """Sleeper returned HTTP 429 and retries were exhausted."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SleeperUnavailable(SleeperError):
    """A transport failure or 5xx that survived every retry."""


class SleeperBadResponse(SleeperError):
    """Sleeper answered with a payload we cannot make sense of."""
