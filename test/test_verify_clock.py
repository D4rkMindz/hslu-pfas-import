"""The injected clock of `pfas_import verify` and the exit code of its result."""
import datetime as dt

import pandas as pd
import pytest
from test_verification import DuckDBTarget, valid_features

from pfas_import.domain.sql_dialect import BIGQUERY, POSTGRES
from pfas_import.verification import checks, verify

DATE_CHECK = "date is not in the future"


class TestRuleChecksSql:
    @pytest.mark.parametrize("dialect", [POSTGRES, BIGQUERY])
    def test_uses_the_given_day(self, dialect):
        sql = checks.rule_checks_sql("t", ["date", "lat", "lon"], dialect, dt.date(2026, 11, 15))

        assert "DATE '2026-11-15'" in sql
        assert "CURRENT_DATE" not in sql.upper()


class TestRunToday:
    @staticmethod
    def date_check(today: dt.date):
        df = valid_features()
        df.loc[0, "date"] = pd.Timestamp("2026-11-20")
        results = {r.check: r for r in verify.run(DuckDBTarget(df), df.copy(), today=today)}
        return results[DATE_CHECK]

    def test_fails_for_a_date_after_today(self):
        assert self.date_check(dt.date(2026, 11, 15)).passed is False

    def test_passes_once_today_is_later(self):
        assert self.date_check(dt.date(2026, 12, 1)).passed is True


class TestVerifyResult:
    @pytest.mark.parametrize(
        ("warnings", "errors", "exit_code"),
        [
            (0, 0, 0),
            (3, 0, 0),
            (0, 1, 1),
            (2, 4, 1),
        ],
    )
    def test_exit_code(self, warnings, errors, exit_code):
        result = verify.VerifyResult(checks=10, passed=10 - warnings - errors, warnings=warnings, errors=errors)

        assert result.exit_code == exit_code
