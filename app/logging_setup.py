import logging
import sys
from pythonjsonlogger import jsonlogger
from ddtrace import tracer


def _inject_trace_id(record: logging.LogRecord) -> logging.LogRecord:
    span = tracer.current_span()
    if span:
        record.trace_id = format(span.trace_id, "032x")
        record.span_id = format(span.span_id, "016x")
        record.service = "shrimpwatch"
    else:
        record.trace_id = "0" * 32
        record.span_id = "0" * 16
        record.service = "shrimpwatch"
    return record


class TraceIdFilter(logging.Filter):
    def filter(self, record):
        _inject_trace_id(record)
        return True


def setup_logging(name: str = "shrimpwatch", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper()))
    logger.addFilter(TraceIdFilter())

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(TraceIdFilter())

    formatter = jsonlogger.JsonFormatter(
        fmt=("%(asctime)s %(levelname)s %(service)s %(name)s " "%(trace_id)s %(span_id)s %(message)s"),
        rename_fields={
            "levelname": "level",
            "asctime": "timestamp",
            "name": "logger",
        },
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    base = setup_logging()
    return base.getChild(name) if name != "shrimpwatch" else base
