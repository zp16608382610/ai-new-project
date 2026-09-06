"""Centralised logging configuration."""
import logging
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging for the application process."""
    logging.basicConfig(
        level=level.upper(),
        format=LOG_FORMAT,
        stream=sys.stdout,
        force=True,
    )