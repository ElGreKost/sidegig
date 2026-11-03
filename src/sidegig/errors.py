"""Errors raised by the board. Each carries a short hint telling the caller what to do next."""


class BoardError(Exception):
    """Base class. str(e) is the message, e.hint the suggested next step."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


class NotFound(BoardError):
    """A board, gig or agent does not exist."""


class Conflict(BoardError):
    """The request is valid but the current state forbids it (already claimed, closed, ...)."""


class Invalid(BoardError):
    """The request itself is malformed (empty text, bad id, bad rating, ...)."""
