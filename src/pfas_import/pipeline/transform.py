"""`pfas_import transform`: turn the latest raw download into the feature file in the import bucket.

- compares the hash of the raw file with the ingestion state
- if it changed, keeps France, extracts the features and the facility tokens and writes them to
  import/last_import.parquet and import/last_facility_tokens.parquet
"""
import logging
import time
from dataclasses import dataclass

from pfas_import.domain.change import ChangePolicy
from pfas_import.domain.features import transform_all
from pfas_import.repository.files import FACILITY_TOKENS_KEY, DatasetFiles
from pfas_import.repository.ingestion_state import IngestionStateRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransformResult:
    changed: bool
    sha256: str
    rows: int
    token_rows: int = 0


def run(
    files: DatasetFiles,
    state: IngestionStateRepository,
    raw_source_file: str,
    target_file: str,
    policy: ChangePolicy | None = None,
    tokens_file: str = FACILITY_TOKENS_KEY,
) -> TransformResult:
    """Read, transform and write the latest downloaded dataset."""
    policy = policy or ChangePolicy()
    started_at = time.monotonic()

    logger.info("========== process_dataset started ==========")
    logger.info("Source object: %s", files.raw_url(raw_source_file))

    source_sha256 = files.raw_sha256(raw_source_file)

    logger.info("Checking ingestion state")
    decision = policy.decide(source_sha256, state.previous_sha256())

    if not decision.changed:
        logger.info("Source unchanged; skipping transformation and publication")
        logger.info(
            "========== process_dataset finished: unchanged (%.1f s) ==========",
            time.monotonic() - started_at,
        )
        return TransformResult(changed=False, sha256=source_sha256, rows=0)

    logger.info("Source changed or not yet ingested; processing dataset")
    raw_df = files.read_raw(raw_source_file)
    logger.info("Available: %s rows", len(raw_df))

    logger.info("Starting feature transformation")
    transform_started_at = time.monotonic()

    output = transform_all(raw_df)
    feature_df, tokens_df = output.features, output.facility_tokens

    logger.info(
        "Feature transformation completed: %s rows, %s columns, %s facility tokens in %.1f s",
        len(feature_df),
        len(feature_df.columns),
        len(tokens_df),
        time.monotonic() - transform_started_at,
    )

    # Release the raw DataFrame before writing the feature files.
    del raw_df

    files.write_import(target_file, feature_df)
    files.write_import(tokens_file, tokens_df)

    logger.info(
        "========== process_dataset finished successfully: "
        "%s rows in %.1f s ==========",
        len(feature_df),
        time.monotonic() - started_at,
    )

    return TransformResult(changed=True, sha256=source_sha256, rows=len(feature_df), token_rows=len(tokens_df))
