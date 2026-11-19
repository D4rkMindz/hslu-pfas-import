"""Decides whether a dataset differs from the last ingested one (used by `pipeline/extract.py` and
`pipeline/transform.py`).

Known bug, kept on purpose until its own fix step: `pipeline/load.py` stores the hash of the import file,
not of the raw file, so the hashes never match and every run reprocesses. See docs/feature-parity.md,
"Change detection never matches".
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ChangeDecision:
    changed: bool
    sha256: str
    previous_sha256: str | None


class ChangePolicy:
    def decide(self, sha256: str, previous_sha256: str | None) -> ChangeDecision:
        return ChangeDecision(
            changed=sha256 != previous_sha256,
            sha256=sha256,
            previous_sha256=previous_sha256,
        )
