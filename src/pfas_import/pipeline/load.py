"""`pfas_import load`: publish the feature file, the facility token file and the ingestion state to the
store TARGET selects.

PostgreSQL writes all three in one transaction. BigQuery has none spanning tables: features first, then the
facility tokens, then the state, so a failure in between only means the next run publishes again
(`repository/ingestion_state.py`).
"""
import logging
import time

from pfas_import.repository.factory import Publication
from pfas_import.repository.files import FACILITY_TOKENS_KEY, IMPORT_SOURCE_KEY, DatasetFiles

logger = logging.getLogger(__name__)


def run(
    files: DatasetFiles,
    publication: Publication,
    import_file: str = IMPORT_SOURCE_KEY,
    tokens_file: str = FACILITY_TOKENS_KEY,
) -> None:
    feature_df = files.read_import(import_file)
    tokens_df = files.read_import(tokens_file)
    features, tokens, state = publication.features, publication.facility_tokens, publication.state

    logger.info("Starting publication to %s and %s", features.name, tokens.name)

    # Known bug, kept until its own fix step: this is the hash of the import file, while extract and
    # transform compare the raw file hash (docs/feature-parity.md, "Change detection never matches").
    source_sha256 = files.import_sha256(import_file)

    transaction_started_at = time.monotonic()
    with state.transaction():
        features.replace(feature_df)
        tokens.replace(tokens_df)
        state.save(source_sha256, import_file)

    logger.info(
        "Publication transaction committed successfully in %.1f s",
        time.monotonic() - transaction_started_at,
    )
