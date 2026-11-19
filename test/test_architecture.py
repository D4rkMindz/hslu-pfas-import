"""Structural rules for the `pfas_import` package, checked on the source code with `ast`.

The modules are parsed, never imported, so the test needs no settings and no infrastructure.

1. The pure layer (`domain/` and `verification/checks.py`) imports no configuration, storage, repository,
   network or Kestra module. It stays testable without I/O, and a change of backend never touches it.
2. Only the repository layer names the tables `pfas_features` and `pfas_ingestion_state`. One place knows
   the table names, so renaming or moving a table touches one module.
3. Only `repository/factory.py` imports `pfas_import.config`. One place reads the configuration; every
   other module receives its values as arguments, so importing a module never needs a `.env`.
4. Only `cli.py` and `logging_setup.py` import `kestra`. The coupling to the orchestrator stays at the
   edge, and the pipeline runs the same way from a terminal.

A failure means code took a shortcut: move the access to where the rule allows it instead of loosening
the rule.
"""
import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

PACKAGE = "pfas_import"
PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / PACKAGE

PURE_FORBIDDEN: tuple[str, ...] = (
    "pfas_import.config",
    "pfas_import.storage",
    "pfas_import.repository",
    "requests",
    "s3fs",
    "fsspec",
    "psycopg",
    "google",
    "kestra",
)
TABLE_NAMES: tuple[str, ...] = ("pfas_features", "pfas_ingestion_state")


@dataclass(frozen=True)
class SourceFile:
    path: Path
    tree: ast.Module

    @property
    def relative(self) -> str:
        """Path relative to the package directory, with forward slashes (e.g. `domain/features.py`)."""
        return self.path.relative_to(PACKAGE_DIR).as_posix()

    @property
    def module(self) -> str:
        parts = list(self.path.relative_to(PACKAGE_DIR).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join([PACKAGE, *parts])

    @property
    def is_package(self) -> bool:
        return self.path.name == "__init__.py"

    def imported_modules(self) -> set[str]:
        """Every module this file imports, anywhere in the file.

        `from a import b` yields both `a` and `a.b`, because `b` may be a submodule
        (`from pfas_import import config`). Relative imports resolve against this file's package.
        """
        modules: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = self._resolve(node)
                modules.add(base)
                modules.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
        return modules

    def _resolve(self, node: ast.ImportFrom) -> str:
        if not node.level:
            return node.module or ""
        package = self.module.split(".")
        if not self.is_package:
            package.pop()
        if node.level > 1:
            package = package[: -(node.level - 1)]
        return ".".join([*package, *([node.module] if node.module else [])])

    def string_constants(self) -> set[str]:
        return {
            node.value
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def imports_any(source: SourceFile, prefixes: tuple[str, ...]) -> list[str]:
    return sorted(m for m in source.imported_modules() if any(_matches(m, p) for p in prefixes))


def _load_sources() -> list[SourceFile]:
    return [
        SourceFile(path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(PACKAGE_DIR.rglob("*.py"))
    ]


@pytest.fixture(scope="module")
def sources() -> list[SourceFile]:
    return _load_sources()


def _report(offenders: dict[str, list[str]]) -> str:
    return "\n".join(f"  {path}: {', '.join(items)}" for path, items in sorted(offenders.items()))


class TestScanner:
    def test_finds_the_package_files(self, sources):
        """The rules below cannot pass vacuously: the scanner sees the real modules."""
        found = {s.relative for s in sources}

        assert {"domain/features.py", "verification/checks.py", "repository/factory.py", "cli.py"} <= found

    @pytest.mark.parametrize(
        ("code", "path", "expected"),
        [
            ("import fsspec.implementations.local", "domain/x.py", "fsspec.implementations.local"),
            ("from pfas_import import config", "domain/x.py", "pfas_import.config"),
            ("from google.cloud import bigquery", "domain/x.py", "google.cloud"),
            ("from ..storage import base", "domain/x.py", "pfas_import.storage.base"),
            ("from . import errors", "domain/__init__.py", "pfas_import.domain.errors"),
            ("def f():\n    import kestra", "domain/x.py", "kestra"),
        ],
    )
    def test_sees_every_import_form(self, code, path, expected):
        """The scanner resolves absolute, `from` and relative imports, also inside functions."""
        source = SourceFile(PACKAGE_DIR / path, ast.parse(code))

        assert expected in source.imported_modules()


class TestPureLayer:
    def test_imports_no_io_config_or_kestra(self, sources):
        """`domain/` and `verification/checks.py` stay free of I/O, configuration and Kestra."""
        pure = [s for s in sources if s.relative.startswith("domain/") or s.relative == "verification/checks.py"]
        assert any(s.relative == "verification/checks.py" for s in pure)

        offenders = {s.relative: found for s in pure if (found := imports_any(s, PURE_FORBIDDEN))}

        assert not offenders, "The pure layer imports I/O, configuration or Kestra modules:\n" + _report(
            offenders
        )


class TestTableNames:
    def test_only_repositories_name_the_tables(self, sources):
        """Only `repository/` holds the table names as string literals."""
        offenders = {
            s.relative: sorted(found)
            for s in sources
            if not s.relative.startswith("repository/")
            and (found := s.string_constants() & set(TABLE_NAMES))
        }

        assert not offenders, "Table names outside repository/:\n" + _report(offenders)


class TestConfig:
    def test_only_the_factory_imports_config(self, sources):
        """`repository/factory.py` is the one module that reads the configuration."""
        offenders = {
            s.relative: found
            for s in sources
            if s.relative != "repository/factory.py" and (found := imports_any(s, ("pfas_import.config",)))
        }

        assert not offenders, "Modules other than repository/factory.py import config:\n" + _report(offenders)


class TestKestra:
    def test_only_the_edge_imports_kestra(self, sources):
        """Only `cli.py` and `logging_setup.py` know about Kestra."""
        allowed = {"cli.py", "logging_setup.py"}
        offenders = {
            s.relative: found
            for s in sources
            if s.relative not in allowed and (found := imports_any(s, ("kestra",)))
        }

        assert not offenders, "Modules other than cli.py and logging_setup.py import kestra:\n" + _report(
            offenders
        )
