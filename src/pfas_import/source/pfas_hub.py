"""Reads the PFAS Data Hub export over HTTP (https://pdh.cnrs.fr/en/download/)."""
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import requests

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB


@dataclass(frozen=True)
class DownloadStream:
    status_code: int
    content_length: int | None  # None if the server sends no usable Content-Length
    chunks: Iterator[bytes]


class PfasHubSource:
    def __init__(self, url: str, chunk_size: int = CHUNK_SIZE, timeout: tuple[int, int] = (15, 300)):
        self.url = url
        self.chunk_size = chunk_size
        self.timeout = timeout

    @contextmanager
    def open(self) -> Iterator[DownloadStream]:
        with requests.get(self.url, stream=True, timeout=self.timeout) as response:
            response.raise_for_status()
            length = response.headers.get("Content-Length", "")
            yield DownloadStream(
                status_code=response.status_code,
                content_length=int(length) if length.isdigit() else None,
                chunks=(chunk for chunk in response.iter_content(chunk_size=self.chunk_size) if chunk),
            )
