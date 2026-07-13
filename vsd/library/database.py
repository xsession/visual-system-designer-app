from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class LibraryDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    vendor TEXT,
                    category TEXT,
                    description TEXT,
                    source_id TEXT NOT NULL,
                    source_revision TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS interfaces (
                    id INTEGER PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    interface_type TEXT NOT NULL,
                    direction TEXT,
                    properties_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(entity_id) REFERENCES entities(id)
                );
                CREATE TABLE IF NOT EXISTS assets (
                    sha256 TEXT PRIMARY KEY,
                    media_type TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    object_path TEXT NOT NULL,
                    original_name TEXT,
                    source_id TEXT,
                    license_id TEXT
                );
                CREATE TABLE IF NOT EXISTS entity_assets (
                    entity_id TEXT NOT NULL,
                    asset_sha256 TEXT NOT NULL,
                    role TEXT NOT NULL,
                    PRIMARY KEY(entity_id, asset_sha256, role)
                );
                CREATE TABLE IF NOT EXISTS graphs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    graph_json TEXT NOT NULL,
                    source_id TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS entity_search USING fts5(
                    entity_id UNINDEXED,
                    name,
                    vendor,
                    category,
                    description,
                    aliases
                );
                """
            )

    def upsert_entity(self, *, entity_id: str, entity_type: str, name: str, vendor: str | None, category: str | None, description: str | None, source_id: str, metadata: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO entities(id, entity_type, name, vendor, category, description, source_id, metadata_json)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    entity_type=excluded.entity_type, name=excluded.name, vendor=excluded.vendor,
                    category=excluded.category, description=excluded.description, source_id=excluded.source_id,
                    metadata_json=excluded.metadata_json, updated_at=CURRENT_TIMESTAMP
                """,
                (entity_id, entity_type, name, vendor, category, description, source_id, json.dumps(metadata, sort_keys=True)),
            )
            db.execute(
                "INSERT INTO entity_search(entity_id, name, vendor, category, description, aliases) VALUES(?, ?, ?, ?, ?, ?)",
                (entity_id, name, vendor or "", category or "", description or "", " ".join(metadata.get("aliases", []))),
            )

    def stats(self) -> dict[str, Any]:
        with self.connect() as db:
            entities = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            assets = db.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
            graphs = db.execute("SELECT COUNT(*) FROM graphs").fetchone()[0]
        return {"entities": entities, "assets": assets, "graphs": graphs}

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        sql = (
            "SELECT e.* FROM entities e JOIN entity_search s ON e.id = s.entity_id "
            "WHERE entity_search MATCH ? LIMIT ?"
            if query
            else "SELECT * FROM entities ORDER BY name LIMIT ?"
        )
        values = (query, limit) if query else (limit,)
        with self.connect() as db:
            rows = db.execute(sql, values).fetchall()
        return [dict(row) | {"metadata": json.loads(row["metadata_json"])} for row in rows]

