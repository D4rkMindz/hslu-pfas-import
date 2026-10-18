"""CLI entrypoint. Run via `uv run pfas_import <command> ...` (see Dockerfile)."""

import argparse

from s3fs import S3FileSystem

from pfas_import import storage
from pfas_import.config import settings as s


def extract(source: str, target_file: str) -> None:
    print(f"Hello world!\nsource={source}\ntarget_file={target_file}")
    fs = S3FileSystem()
    raw = fs.exists(s.bucket_raw)
    imprt = fs.exists(s.bucket_import)
    file = fs.isfile(s.bucket_raw + '/' + target_file)
    print(f"raw: {raw}\nimport: {imprt}\nfile: {file}")


def transform(raw_source_file: str, target_file: str, schema_name: str) -> None:
    pass


def load(source_file: str, target_schema_name: str) -> None:
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pfas_import")
    subcommands = parser.add_subparsers(dest="command", required=True)

    extract_parser = subcommands.add_parser(
        "extract", help="Download a file from `source` to `target-file`."
    )
    extract_parser.add_argument("-s", "--source", type=str, required=True, help="Source URL, e.g. https://hslu.ch")
    extract_parser.add_argument("-t", "--target-file", type=str, required=True,
                                help="Target file in S3_RAW_BUCKET, e.g. export.parquet")

    transform_parser = subcommands.add_parser(
        "transform",
        help="Clean up values in `raw-source-file` into `target-file` using `schema-name`.",
    )
    transform_parser.add_argument("-r", "--raw-source-file", type=str, required=True,
                                  help="Source file in S3_RAW_BUCKET, e.g. export.parquet")
    transform_parser.add_argument("-t", "--target-file", type=str, required=True,
                                  help="Target file in S3_IMPORT_BUCKET, e.g. data.parquet")
    transform_parser.add_argument("-n", "--schema-mame", type=str, required=True,
                                  help="Target schema name for the transformation")

    load_parser = subcommands.add_parser(
        "load", help="Load `source-file` into the `target-schema-name` database schema."
    )
    load_parser.add_argument("-f", "--source-file", type=str, required=True,
                             help="Source file in S3_IMPORT_BUCKET, e.g. data.parquet")
    load_parser.add_argument("-n", "--schema-name", type=str, required=True,
                             help="Target schema name which the transformation was built for (same as transform --schema-name)")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    storage.setup()

    match args.command:
        case "extract":
            extract(args.source, args.target_file)
        case "transform":
            transform(args.raw_source_file, args.target_file, args.schema_name)
        case "load":
            load(args.source_file, args.target_schema_name)


if __name__ == "__main__":
    main()
