"""Log configuration for the server process.

Without this nothing configures Python logging: info lines from Tome
(auto-import, Hardcover sync, restore) are dropped, and warnings arrive with
no time or level. uvicorn sets up only its own loggers.
"""
import logging
import sys

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _StderrHandler(logging.StreamHandler):
    """Writes to whatever sys.stderr is at the time of the log call, so a
    stream swapped out later (test capture, a daemoniser) is never a closed
    file."""

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    @property
    def stream(self):
        return sys.stderr


def configure_logging(level: str) -> None:
    """Give the root logger a handler and set the level of Tome's own loggers.

    Libraries stay at WARNING: some log request URLs at INFO, and those can
    carry API keys. Calling it again only updates the level.
    """
    root = logging.getLogger()
    # Leave the root logger alone when something else (pytest, a host
    # application) already set it up.
    if not root.handlers:
        handler = _StderrHandler()
        handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(handler)
        root.setLevel(logging.WARNING)
    logging.getLogger("backend").setLevel(getattr(logging, level, logging.INFO))
