"""`logging_setup.configure`: text lines on stdout, or Kestra log lines split by level."""
import json
import logging

import pytest

from pfas_import import logging_setup

FMT = "%(levelname)s|%(name)s|%(message)s"


def _is_pytest_handler(handler: logging.Handler) -> bool:
    return type(handler).__module__.startswith("_pytest")


@pytest.fixture
def root_logger():
    """Save the root logger's handlers, level and global disable level, and restore them afterwards.

    pytest adds its own capture handlers per test phase and removes them itself, so the fixture leaves
    those alone and restores only the others.
    """
    root = logging.getLogger()
    saved_handlers = [h for h in root.handlers if not _is_pytest_handler(h)]
    saved_level = root.level
    saved_disable = root.manager.disable
    logging.disable(logging.NOTSET)

    yield root

    for handler in list(root.handlers):
        if not _is_pytest_handler(handler):
            root.removeHandler(handler)
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)
    logging.disable(saved_disable)


def kestra_logs(stream: str) -> list[dict]:
    """The entries of every `::{"logs": [...]}::` line in `stream`."""
    entries = []
    for line in stream.splitlines():
        if line.startswith("::") and line.endswith("::"):
            entries += json.loads(line[2:-2])["logs"]
    return entries


class TestTextMode:
    def test_writes_formatted_lines_to_stdout(self, root_logger, capsys):
        logging_setup.configure("INFO", FMT, "text")

        logging.getLogger("pfas_import.x").info("hello")
        logging.getLogger("pfas_import.x").warning("careful")

        out, err = capsys.readouterr()
        assert out.splitlines() == ["INFO|pfas_import.x|hello", "WARNING|pfas_import.x|careful"]
        assert err == ""

    def test_respects_the_level(self, root_logger, capsys):
        logging_setup.configure("WARNING", FMT, "text")

        logging.getLogger("pfas_import.x").info("hidden")

        assert capsys.readouterr().out == ""


class TestKestraMode:
    def test_info_goes_to_stdout_as_a_kestra_line(self, root_logger, capsys):
        logging_setup.configure("INFO", FMT, "kestra")

        logging.getLogger("pfas_import.x").info("hello")

        out, err = capsys.readouterr()
        assert kestra_logs(out) == [{"level": "INFO", "message": "pfas_import.x :: hello"}]
        assert err == ""

    @pytest.mark.parametrize(
        ("level", "kestra_level"),
        [
            (logging.WARNING, "WARN"),
            (logging.ERROR, "ERROR"),
        ],
    )
    def test_warning_and_above_go_to_stderr(self, root_logger, capsys, level, kestra_level):
        logging_setup.configure("INFO", FMT, "kestra")

        logging.getLogger("pfas_import.x").log(level, "hello")

        out, err = capsys.readouterr()
        assert kestra_logs(err) == [{"level": kestra_level, "message": "pfas_import.x :: hello"}]
        assert kestra_logs(out) == []


class TestReconfigure:
    @pytest.mark.parametrize("mode", ["text", "kestra"])
    def test_second_call_replaces_the_handlers(self, root_logger, capsys, mode):
        logging_setup.configure("INFO", FMT, mode)
        logging_setup.configure("INFO", FMT, mode)

        logging.getLogger("pfas_import.x").info("hello")

        assert capsys.readouterr().out.count("hello") == 1

    def test_removes_handlers_from_before(self, root_logger, capsys):
        foreign = logging.NullHandler()
        root_logger.addHandler(foreign)

        logging_setup.configure("INFO", FMT, "text")

        assert foreign not in root_logger.handlers
