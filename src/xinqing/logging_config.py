"""Centralised structlog configuration for all xinqing services.

Call ``setup_logging()`` once at process start (in app.py, alert.py,
data_service.py).  Imported modules should only do::

    import structlog
    logger = structlog.get_logger("xinqing.graph")

Never call ``setup_logging()`` from a library module that is imported by
an entry point — the entry point owns the configuration.
"""

from __future__ import annotations

import logging
import os

import structlog


def setup_logging() -> None:
    """Configure structlog to emit JSON lines to stdout.

    The log level is read from ``LOG_LEVEL`` (default ``INFO``).
    Standard-library logging (used by langchain/flask internals) is
    bridged through the same renderer so all output shares one format.
    """

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        level=level,
        format="%(message)s",
        force=True,
    )