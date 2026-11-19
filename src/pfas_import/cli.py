"""CLI entrypoint. Run via `uv run pfas_import [--output text|kestra] <command> ...` (see Dockerfile).

The only module that emits Kestra outputs (enforced by test/test_architecture.py): with `--output kestra`
each step's result becomes the task outputs, with `--output text` (default) one log line.
"""
import argparse
import logging
import sys
from dataclasses import asdict
from typing import Any

from kestra import Kestra

from pfas_import.pipeline import extract, load, transform
from pfas_import.repository import factory
from pfas_import.repository.files import IMPORT_SOURCE_KEY, RAW_SOURCE_KEY
from pfas_import.verification import verify

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pfas_import")
    parser.add_argument("--output", choices=["text", "kestra"], default="text",
                        help="text: log lines for a terminal (default). kestra: Kestra log lines and task "
                             "outputs (changed, sha256, rows, ...), used by the Kestra flow.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    extract_parser = subcommands.add_parser(
        "extract", help="Download a file from `source` to `target-file`."
    )
    extract_parser.add_argument("-s", "--source", type=str, required=False,
                                help="Source URL, default: settings.dataset_url = https://pdh.cnrs.fr/download/full.parquet",
                                default=factory.default_dataset_url())
    extract_parser.add_argument("-r", "--target-file", type=str, required=False,
                                help="Target file in S3_RAW_BUCKET, default: last_download.parquet",
                                default=RAW_SOURCE_KEY)

    transform_parser = subcommands.add_parser(
        "transform",
        help="Clean up values in `raw-source-file` into `target-file`.",
    )
    transform_parser.add_argument("-r", "--raw-source-file", type=str, required=False,
                                  help="Source file in S3_RAW_BUCKET, default: last_download.parquet",
                                  default=RAW_SOURCE_KEY)
    transform_parser.add_argument("-t", "--target-file", type=str, required=False,
                                  help="Target file in S3_IMPORT_BUCKET, default: last_import.parquet",
                                  default=IMPORT_SOURCE_KEY)

    load_parser = subcommands.add_parser(
        "load", help="Load `source-file` into the `target-schema-name` database schema."
    )
    load_parser.add_argument("-t", "--source-file", type=str, required=False,
                             help="Source file in S3_IMPORT_BUCKET, default: last_import.parquet",
                             default=IMPORT_SOURCE_KEY)
    load_parser.add_argument("--target", choices=["local", "online"], required=False,
                             help="Publish to the local PostgreSQL database or the online BigQuery "
                                  "warehouse, default: LOAD_TARGET (local)",
                             default=factory.default_load_target())

    verify_parser = subcommands.add_parser(
        "verify",
        help="Check that the published feature table equals `source-file` and holds plausible values. "
             "Exit code 1 if an error check fails.",
    )
    verify_parser.add_argument("-t", "--source-file", type=str, required=False,
                               help="Source file in S3_IMPORT_BUCKET, default: last_import.parquet",
                               default=IMPORT_SOURCE_KEY)
    verify_parser.add_argument("--target", choices=["local", "online"], required=False,
                               help="Verify the local PostgreSQL database or the online BigQuery "
                                    "warehouse, default: LOAD_TARGET (local)",
                               default=factory.default_load_target())
    verify_parser.add_argument("--print-sql", action="store_true",
                               help="Only print the SQL of the rule checks (e.g. for pgAdmin), run nothing.")

    return parser


def emit(command: str, result: Any, output: str) -> None:
    values = asdict(result)
    if output == "kestra":
        Kestra.outputs(values)
    else:
        logger.info("%s result: %s", command, " ".join(f"{key}={value}" for key, value in values.items()))


def main() -> None:
    args = build_parser().parse_args()
    factory.configure_logging(args.output)
    factory.ensure_buckets()

    match args.command:
        case "extract":
            with factory.ingestion_state() as state:
                result = extract.run(
                    factory.pfas_hub(args.source), factory.dataset_files(), state, args.target_file
                )
            emit(args.command, result, args.output)
        case "transform":
            with factory.ingestion_state() as state:
                result = transform.run(
                    factory.dataset_files(), state, args.raw_source_file, args.target_file
                )
            emit(args.command, result, args.output)
        case "load":
            with factory.publication(args.target) as publication:
                load.run(factory.dataset_files(), publication, args.source_file)
        case "verify":
            if args.print_sql:
                sys.exit(verify.print_sql(args.target))
            result = verify.main(args.source_file, args.target)
            emit(args.command, result, args.output)
            sys.exit(result.exit_code)


if __name__ == "__main__":
    main()
