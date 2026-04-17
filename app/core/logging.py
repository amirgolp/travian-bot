"""Structured logging.

One configuration, many loggers. Every log line is a single JSON-ish record
with fields — look them up in a dashboard by `event`, `account`, `village_id`,
`controller`, etc. In dev, `ConsoleRenderer` colorizes them for a tty.

Levels:
  DEBUG  — per-step trace (selectors tried, page navigated, counters bumped)
  INFO   — business events (login ok, farmlist dispatched, reconcile summary)
  WARNING — recoverable oddities (farmlist not found on page, selector missed)
  ERROR  — failures the user should see (login failed, worker auto-disabled)

Account context:
  `bind_account(label, id)` context manager stamps every log inside its scope
  with `account=<label>` and `account_id=<id>`. The AccountWorker wraps its
  per-account loop in this so logs from many parallel accounts stay readable.
"""
from __future__ import annotations

import logging
import sys
from contextlib import contextmanager

import structlog

from app.core.config import get_settings


def configure_logging() -> None:
    level = getattr(logging, get_settings().log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            # pad_level=False drops the default 8-char padding on the level
            # column (was emitting `[info     ]` with 5 trailing spaces).
            structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty(), pad_level=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)


@contextmanager
def bind_account(label: str, account_id: int):
    """Tag every log emitted while in-scope with account fields."""
    token = structlog.contextvars.bind_contextvars(account=label, account_id=account_id)
    try:
        yield
    finally:
        # bind_contextvars returns a dict of tokens keyed by var name in newer structlog
        # versions. reset_contextvars handles both shapes.
        try:
            structlog.contextvars.reset_contextvars(**token)
        except Exception:
            structlog.contextvars.unbind_contextvars("account", "account_id")
