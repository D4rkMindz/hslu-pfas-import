"""`cli.emit` in both output modes and the `--output` option of `cli.build_parser`."""
import json
import logging
from dataclasses import dataclass

import pytest

from pfas_import import cli


@dataclass(frozen=True)
class StepResult:
    changed: bool
    sha256: str
    rows: int


RESULT = StepResult(changed=True, sha256="abc123", rows=3)


def kestra_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("::") and line.endswith("::")]


class TestEmit:
    def test_kestra_prints_the_fields_as_outputs(self, capsys):
        cli.emit("extract", RESULT, "kestra")

        lines = kestra_lines(capsys.readouterr().out)
        assert len(lines) == 1
        assert json.loads(lines[0][2:-2]) == {"outputs": {"changed": True, "sha256": "abc123", "rows": 3}}

    def test_text_logs_one_line_and_prints_no_outputs(self, capsys, caplog):
        caplog.set_level(logging.INFO)

        cli.emit("extract", RESULT, "text")

        assert kestra_lines(capsys.readouterr().out) == []
        messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert messages == ["extract result: changed=True sha256=abc123 rows=3"]


class TestBuildParser:
    @pytest.fixture(autouse=True)
    def settings(self, install_settings):
        install_settings()

    def test_output_before_the_subcommand(self):
        args = cli.build_parser().parse_args(["--output", "kestra", "extract"])

        assert args.output == "kestra"
        assert args.command == "extract"

    @pytest.mark.parametrize("command", ["extract", "transform", "load", "verify"])
    def test_output_defaults_to_text(self, command):
        assert cli.build_parser().parse_args([command]).output == "text"

    @pytest.mark.parametrize("value", ["json", "TEXT", ""])
    def test_rejects_other_values(self, value, capsys):
        with pytest.raises(SystemExit) as exit_info:
            cli.build_parser().parse_args(["--output", value, "extract"])

        assert exit_info.value.code == 2
        assert "--output" in capsys.readouterr().err
