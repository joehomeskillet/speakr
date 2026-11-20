"""Clinic images can deliver different browser assets under the same Speakr release."""
from functools import cache
from hashlib import file_digest, sha256
from pathlib import Path


@cache
def clinic_asset_version(release: str, static_folder: str) -> str:
    """Fingerprint the installed public assets once per worker, independent of checkout and mtimes."""
    root = Path(static_folder)
    digest = sha256()
    for entry in sorted(root.iterdir()):
        paths = sorted(entry.rglob("*")) if entry.is_dir() else (entry,)
        for path in paths:
            if path.is_file():
                digest.update(path.relative_to(root).as_posix().encode() + b"\0")
                with path.open("rb") as asset:
                    digest.update(file_digest(asset, "sha256").digest())
    return f"{release}-{digest.hexdigest()[:20]}"
