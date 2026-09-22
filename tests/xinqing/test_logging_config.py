"""Smoke tests for logging_config.setup_logging()."""

import io
import json
import logging
import os

import structlog


def test_setup_logging_does_not_raise():
    from xinqing.logging_config import setup_logging

    setup_logging()
    logger = structlog.get_logger("test")
    logger.info("smoke_test", ok=True)


def test_log_level_from_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    from xinqing.logging_config import setup_logging

    setup_logging()
    logger = structlog.get_logger("test")
    logger.debug("debug_visible", value=42)