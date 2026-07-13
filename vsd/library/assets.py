from __future__ import annotations

import hashlib
import mimetypes
import shutil
from pathlib import Path

from .database import LibraryDatabase


def ingest_asset(db: LibraryDatabase, source: Path, objects_dir: Path, source_id: str, license_id: str | None = None) -> str:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    target = objects_dir / "sha256" / digest[:2] / digest[2:4] / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)
    media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO assets(sha256, media_type, byte_size, object_path, original_name, source_id, license_id)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sha256) DO NOTHING
            """,
            (digest, media_type, source.stat().st_size, str(target), source.name, source_id, license_id),
        )
    return digest

