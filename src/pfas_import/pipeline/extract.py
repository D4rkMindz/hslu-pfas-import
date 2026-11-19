"""`pfas_import extract`: download the PFAS-Hub dataset to the raw bucket and detect whether it changed.

- streams the parquet from the PFAS hub to raw/last_download.parquet while hashing it
- compares the hash with the ingestion state and removes the download if it is unchanged
"""
import hashlib
import logging
from dataclasses import dataclass

from pfas_import.domain.change import ChangePolicy
from pfas_import.repository.files import DatasetFiles
from pfas_import.repository.ingestion_state import IngestionStateRepository
from pfas_import.source.pfas_hub import PfasHubSource

logger = logging.getLogger(__name__)

MIB = 1024 * 1024
LOG_INTERVAL_BYTES = 100 * MIB


@dataclass(frozen=True)
class DownloadResult:
    changed: bool
    sha256: str
    size_bytes: int


def run(
    source: PfasHubSource,
    files: DatasetFiles,
    state: IngestionStateRepository,
    target_file: str,
    policy: ChangePolicy | None = None,
) -> DownloadResult:
    logger.info("PFAS-Hub download task started.")

    result = _download(source, files, state, target_file, policy or ChangePolicy())

    logger.info("Download task finished: changed=%s, size_bytes=%s.", result.changed, result.size_bytes)
    return result


def _download(
    source: PfasHubSource,
    files: DatasetFiles,
    state: IngestionStateRepository,
    target_file: str,
    policy: ChangePolicy,
) -> DownloadResult:
    """Download to a fixed key and detect changes."""
    logger.info("Starting PFAS-Hub dataset download.")
    logger.info("Source URL: %s", source.url)
    logger.info("Destination: %s", files.raw_url(target_file))

    sha256 = hashlib.sha256()
    size_bytes = 0
    next_log_bytes = LOG_INTERVAL_BYTES
    download_complete = False

    try:
        logger.info("Connecting to the dataset source.")

        with source.open() as download:
            logger.info(
                "HTTP response received: status=%s, content_length=%s.",
                download.status_code,
                "unknown" if download.content_length is None else f"{download.content_length / MIB:.2f} MiB",
            )
            logger.info("Streaming dataset to %s.", files.raw_url(target_file))

            with files.open_raw_for_writing(target_file) as target:
                for chunk in download.chunks:
                    target.write(chunk)
                    sha256.update(chunk)
                    size_bytes += len(chunk)

                    if size_bytes >= next_log_bytes:
                        logger.info("Download progress: %.1f MiB transferred.", size_bytes / MIB)
                        next_log_bytes = (size_bytes // LOG_INTERVAL_BYTES + 1) * LOG_INTERVAL_BYTES

        download_complete = True
        file_hash = sha256.hexdigest()

        logger.info("Download complete: %.1f MiB; SHA-256: %s", size_bytes / MIB, file_hash)

        decision = policy.decide(file_hash, state.previous_sha256())

        if not decision.changed:
            logger.info("Dataset unchanged; ingestion will be skipped.")
            files.remove_raw(target_file)
            logger.info("Removed unchanged download: %s", files.raw_url(target_file))

            return DownloadResult(changed=False, sha256=file_hash, size_bytes=size_bytes)

        logger.info("Dataset has changed; ready for validation and import.")

        return DownloadResult(changed=True, sha256=file_hash, size_bytes=size_bytes)

    except Exception:
        logger.exception("Dataset download or hash comparison failed.")
        raise

    finally:
        if not download_complete:
            try:
                if files.raw_exists(target_file):
                    files.remove_raw(target_file)
                    logger.info("Removed incomplete download: %s", files.raw_url(target_file))
            except Exception:
                logger.exception("Could not remove incomplete download: %s", target_file)
