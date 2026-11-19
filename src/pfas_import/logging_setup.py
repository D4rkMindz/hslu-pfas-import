"""Configures the root logger once per CLI run (called through `repository/factory.py`).

- text: LOG_FORMAT lines on stdout.
- kestra: Kestra's `::{"logs": [...]}::` lines, so Kestra shows the real log level. INFO and below go to
  stdout, WARNING and above to stderr, like `Kestra.logger()`. Kestra adds its own timestamp.
"""
import logging
import sys
from typing import Literal

from kestra import JsonFormatter, LogFormatter

OutputMode = Literal["text", "kestra"]


class _KestraFormatter(JsonFormatter):
    # Kestra's default "%(asctime)s - %(message)s" drops the logger name.
    _formatter = LogFormatter("%(name)s :: %(message)s")


def configure(level: str, fmt: str, mode: OutputMode) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(level)

    if mode == "text":
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(handler)
        return

    stdout = logging.StreamHandler(sys.stdout)
    stdout.addFilter(lambda record: record.levelno <= logging.INFO)
    stdout.setFormatter(_KestraFormatter())

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(logging.WARNING)
    stderr.setFormatter(_KestraFormatter())

    root.addHandler(stdout)
    root.addHandler(stderr)
