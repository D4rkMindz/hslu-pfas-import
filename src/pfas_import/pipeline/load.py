"""`pfas_import load`: publish the feature file to the feature store LOAD_TARGET selects.

PostgreSQL gets the features and the ingestion state in one transaction. BigQuery only gets the features.
"""
import logging
import time

from pfas_import.repository.factory import Publication
from pfas_import.repository.files import IMPORT_SOURCE_KEY, DatasetFiles

logger = logging.getLogger(__name__)


def run(files: DatasetFiles, publication: Publication, import_file: str = IMPORT_SOURCE_KEY) -> None:
    feature_df = files.read_import(import_file)
    features, state = publication.features, publication.state

    logger.info("Starting publication to %s", features.name)

    if state is None:
        features.replace(feature_df)
        return

    # Known bug, kept until its own fix step: this is the hash of the import file, while extract and
    # transform compare the raw file hash (docs/feature-parity.md, "Change detection never matches").
    source_sha256 = files.import_sha256(import_file)

    transaction_started_at = time.monotonic()
    with state.transaction():
        features.replace(feature_df)
        state.save(source_sha256, import_file)

    logger.info(
        "Publication transaction committed successfully in %.1f s",
        time.monotonic() - transaction_started_at,
    )
