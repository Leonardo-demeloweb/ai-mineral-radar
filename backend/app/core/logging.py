"""
Structured Logging Configuration
================================

Uses structlog for structured, contextual logging.
Supports JSON (production) and console (development) formats.
"""

import logging
import sys
from typing import Any

import structlog
from structlog.types import Processor

from app.core.config import settings


def configure_logging() -> None:
    """
    Configure structured logging for the application.
    
    Call this once at application startup.
    """
    # Shared processors for all outputs
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.log_format == "json":
        # JSON format for production
        processors = shared_processors + [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Console format for development
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard logging to use structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.getLevelName(settings.log_level),
    )

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)
    logging.getLogger("pymongo.topology").setLevel(logging.WARNING)
    logging.getLogger("pymongo.connection").setLevel(logging.WARNING)
    logging.getLogger("pymongo.serverSelection").setLevel(logging.WARNING)
    logging.getLogger("pymongo.command").setLevel(logging.WARNING)

    # File handler for MCP/tool diagnostics (survives terminal buffer overflow)
    _add_file_handler("langgraph.tools", "mcp_calls.log")
    _add_file_handler("langgraph.graph", "mcp_calls.log")
    _add_file_handler("mcp.provider", "mcp_calls.log")


def _add_file_handler(logger_name: str, filename: str) -> None:
    """Attach a file handler to a standard-library logger."""
    import pathlib
    log_path = pathlib.Path(__file__).resolve().parent.parent.parent / filename
    fh = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    logging.getLogger(logger_name).addHandler(fh)


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """
    Get a structured logger instance.
    
    Args:
        name: Logger name (usually __name__)
    
    Returns:
        Configured structlog logger
    
    Usage:
        from app.core.logging import get_logger
        
        logger = get_logger(__name__)
        logger.info("User logged in", user_id="123", ip="192.168.1.1")
    """
    return structlog.get_logger(name)


def log_context(**kwargs: Any) -> None:
    """
    Add context to all subsequent log calls in the current context.
    
    Usage:
        log_context(request_id="abc123", user_id="user456")
        logger.info("Processing request")  # Will include request_id and user_id
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_log_context() -> None:
    """Clear all bound context variables."""
    structlog.contextvars.clear_contextvars()
