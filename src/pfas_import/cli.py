"""CLI entrypoint. Run via `uv run pfas_import <command> ...` (see Dockerfile)."""

import argparse

from pfas_import.ingestion import download, load, process_dataset


def extract(source, target_file) -> None:
    download.main(source, target_file)


def transform(raw_source_file: str, target_file: str) -> None:
    process_dataset.main(raw_source_file, target_file)

def load_data(source_file: str) -> None:
    load.main(source_file)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pfas_import")
    subcommands = parser.add_subparsers(dest="command", required=True)

    extract_parser = subcommands.add_parser(
        "extract", help="Download a file from `source` to `target-file`."
    )
    extract_parser.add_argument("-s", "--source", type=str, required=False,
                                help="Source URL, default: settings.dataset_url = https://pdh.cnrs.fr/download/full.parquet",
                                default=settings.dataset_url)
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

    return parser


def main() -> None:
    args = build_parser().parse_args()
    storage.setup()

    match args.command:
        case "extract":
            extract(args.source, args.target_file)
        case "transform":
            transform(args.raw_source_file, args.target_file)
        case "load":
            load_data(args.source_file)


if __name__ == "__main__":
    main()
