"""`pfas_import verify`: checks that the published feature table is complete and plausible.

Runs on its own at any time: it reads the import file and the published table, nothing else.
Exit code 1 if an error check fails; warnings are only logged.
"""
import datetime as dt
import logging
import time
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

import pandas as pd

from pfas_import.domain.sql_dialect import Dialect
from pfas_import.repository import factory
from pfas_import.repository.files import IMPORT_SOURCE_KEY
from pfas_import.verification import checks
from pfas_import.verification.checks import CheckResult, Severity

logger = logging.getLogger(__name__)


class PublishedTable(Protocol):
    dialect: Dialect
    name: str  # human-readable table name for the log
    table_sql: str  # table reference to use in SQL

    def columns(self) -> list[str]:
        """Column names in table order; empty if the table does not exist."""
        ...

    def query(self, sql: str) -> list[Sequence[Any]]: ...


@dataclass(frozen=True)
class VerifyResult:
    checks: int
    passed: int
    warnings: int
    errors: int

    @property
    def exit_code(self) -> int:
        """0 all error checks passed, 1 at least one failed."""
        return 1 if self.errors else 0


def open_target() -> AbstractContextManager[PublishedTable]:
    """The published table of TARGET: PostgreSQL (local) or BigQuery (cloud)."""
    return factory.published_features()


def read_import_file(source_file: str) -> pd.DataFrame:
    files = factory.dataset_files()
    logger.info("Reading import file %s", files.import_url(source_file))
    return files.read_import(source_file)


def _today(today: dt.date | None) -> dt.date:
    return today or dt.datetime.now(dt.UTC).date()


def run(target: PublishedTable, expected: pd.DataFrame, today: dt.date | None = None) -> list[CheckResult]:
    """All checks of `target` against the import file content `expected`."""
    table_columns = target.columns()
    if not table_columns:
        return [CheckResult("published table exists", Severity.ERROR, 1, f"{target.name} not found")]

    results = [CheckResult("published table exists", Severity.ERROR, 0)]
    results += checks.check_schema(table_columns)

    file_columns = [str(c) for c in expected.columns]
    common = [c for c in file_columns if c in table_columns]
    numeric = [c for c in checks.numeric_columns(expected) if c in common]
    row = target.query(checks.reconciliation_sql(target.table_sql, common, numeric, target.dialect))[0]
    results += checks.compare(
        file_columns,
        table_columns,
        checks.file_aggregates(expected, common, numeric),
        checks.table_aggregates(row, common, numeric),
    )

    results += checks.rule_results(
        target.query(checks.rule_checks_sql(target.table_sql, table_columns, target.dialect, _today(today)))
    )
    return results


def report(results: list[CheckResult]) -> tuple[int, int]:
    """Log one line per check and a summary; returns (errors, warnings)."""
    for result in results:
        line = f"{result.status}  {result.check}"
        if not result.passed:
            line += f"  ({result.failures}{': ' + result.detail if result.detail else ''})"
        elif result.detail:
            line += f"  ({result.detail})"

        if result.passed:
            logger.info(line)
        elif result.severity is Severity.ERROR:
            logger.error(line)
        else:
            logger.warning(line)

    passed = sum(r.passed for r in results)
    errors = sum(not r.passed and r.severity is Severity.ERROR for r in results)
    warnings = sum(not r.passed and r.severity is Severity.WARNING for r in results)
    logger.info("%s of %s checks passed, %s warnings, %s errors", passed, len(results), warnings, errors)
    return errors, warnings


def print_sql(today: dt.date | None = None) -> int:
    """Print the SQL of the rule checks (e.g. for pgAdmin) and run nothing. Returns the exit code."""
    # stdout must only hold the SQL here.
    logging.disable(logging.INFO)

    with open_target() as published:
        # The rule checks depend on the columns (tier-2 prefixes), so they are read from the table.
        columns = published.columns()
        if not columns:
            logger.error("%s not found", published.name)
            return 1
        print(checks.rule_checks_sql(published.table_sql, columns, published.dialect, _today(today)))
        return 0


def main(source_file: str = IMPORT_SOURCE_KEY, today: dt.date | None = None) -> VerifyResult:
    started_at = time.monotonic()

    with open_target() as published:
        expected = read_import_file(source_file)
        logger.info("Verifying %s against %s (%s rows)", published.name, source_file, len(expected))
        results = run(published, expected, today)

    errors, warnings = report(results)
    logger.info("Verification finished in %.1f s", time.monotonic() - started_at)
    return VerifyResult(
        checks=len(results),
        passed=sum(r.passed for r in results),
        warnings=warnings,
        errors=errors,
    )
