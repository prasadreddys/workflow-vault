"""SHA-256 hashing helpers for Workflow Vault.

Every stored workflow file is hashed at add time and the hash is recorded
in the vault manifest. `verify` recomputes the hash and compares it, so a
workflow that was edited after being added is caught as CHANGED.
"""

import hashlib

HASH_ALGO = "sha256"


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    """Return the hex SHA-256 digest of the file at ``path``."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_short(digest: str, length: int = 12) -> str:
    """Return the first ``length`` hex chars of a digest, for display."""
    return digest[:length]
