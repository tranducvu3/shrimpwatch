"""Log JSON ra stdout, moi dong mang trace_id/span_id de Datadog noi log voi trace."""

import logging
import sys

from ddtrace import tracer
from pythonjsonlogger import jsonlogger


class TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        span = tracer.current_span()
        record.trace_id = format(span.trace_id, "032x") if span else "0" * 32
        record.span_id = format(span.span_id, "016x") if span else "0" * 16
        record.service = "shrimpwatch"
        return True


def setup_logging(name: str = "shrimpwatch", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper()))
    logger.addFilter(TraceIdFilter())

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(TraceIdFilter())
    handler.setFormatter(
        jsonlogger.JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(service)s %(name)s %(trace_id)s %(span_id)s %(message)s",
            rename_fields={"levelname": "level", "asctime": "timestamp", "name": "logger"},
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    base = setup_logging()
    return base.getChild(name) if name != "shrimpwatch" else base
