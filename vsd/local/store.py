from __future__ import annotations

import csv
import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Iterator, Sequence

from .models import AssetRecord, ComponentRecord, TelemetryPoint


SCHEMA_VERSION = 1


class LocalStore:
    """SQLite metadata store plus content-addressed local asset storage.

    Each operation uses a short-lived connection. This works reliably across the
    FastAPI threadpool, the asyncio event loop and worker processes. SQLite WAL
    mode allows readers to continue while telemetry is being ingested.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.db_path = self.root / "vsd-local.db"
        self.assets_root = self.root / "assets" / "sha256"
        self.exports_root = self.root / "exports"
        self.logs_root = self.root / "logs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets_root.mkdir(parents=True, exist_ok=True)
        self.exports_root.mkdir(parents=True, exist_ok=True)
        self.logs_root.mkdir(parents=True, exist_ok=True)
        self._schema_lock = threading.Lock()
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._schema_lock, self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("PRAGMA synchronous = NORMAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS components (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    vendor TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    bus TEXT NOT NULL,
                    class_name TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_components_kind_rank ON components(kind, rank);
                CREATE INDEX IF NOT EXISTS idx_components_vendor ON components(vendor);
                CREATE TABLE IF NOT EXISTS assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sha256 TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    mime TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    stored_path TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    graph_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    target TEXT NOT NULL,
                    firmware_asset_id INTEGER,
                    status TEXT NOT NULL,
                    command_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    started_at REAL NOT NULL,
                    stopped_at REAL,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL,
                    FOREIGN KEY(firmware_asset_id) REFERENCES assets(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    ts REAL NOT NULL,
                    channel TEXT NOT NULL,
                    value_num REAL,
                    value_text TEXT,
                    kind TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_telemetry_session_channel_ts
                    ON telemetry(session_id, channel, ts);
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    ts REAL NOT NULL,
                    level TEXT NOT NULL,
                    source TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_logs_session_ts ON logs(session_id, ts);
                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY,
                    asset_id INTEGER NOT NULL,
                    architecture TEXT,
                    report_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS saved_filters (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    expression TEXT NOT NULL DEFAULT '',
                    pipeline_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            db.execute(
                "INSERT INTO metadata(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def import_component_library(self, path: str | Path) -> int:
        return self.import_component_catalog(path) if Path(path).suffix.lower() == ".csv" else self.import_component_json(path)

    def import_component_catalog(self, csv_path: str | Path) -> int:
        path = csv_path if hasattr(csv_path, "open") else Path(csv_path)
        if isinstance(path, Path) and not path.exists():
            raise FileNotFoundError(path)
        count = 0
        with path.open(newline="", encoding="utf-8-sig") as handle, self.connect() as db:
            for row in csv.DictReader(handle):
                slug = row["id"].strip().lower()
                component_id = f"{row['kind'].strip().lower()}:{slug}"
                db.execute(
                    """
                    INSERT INTO components(id, kind, rank, model, vendor, tier, bus, class_name, metadata_json)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        kind=excluded.kind, rank=excluded.rank, model=excluded.model,
                        vendor=excluded.vendor, tier=excluded.tier, bus=excluded.bus,
                        class_name=excluded.class_name, metadata_json=excluded.metadata_json
                    """,
                    (
                        component_id,
                        row["kind"],
                        int(row["rank"]),
                        row["model"],
                        row["vendor"],
                        row["tier"],
                        row["bus"],
                        row["class"],
                        json.dumps({"source": "renode-external-components-catalog", "slug": slug}),
                    ),
                )
                count += 1
        return count

    def import_component_json(self, json_path: str | Path) -> int:
        path = Path(json_path)
        if not path.exists():
            raise FileNotFoundError(path)
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        components = self._component_items_from_json(payload)
        count = 0
        with self.connect() as db:
            for rank, item in enumerate(components, start=1):
                normalized = self._normalize_component_item(item, rank)
                db.execute(
                    """
                    INSERT INTO components(id, kind, rank, model, vendor, tier, bus, class_name, metadata_json)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        kind=excluded.kind, rank=excluded.rank, model=excluded.model,
                        vendor=excluded.vendor, tier=excluded.tier, bus=excluded.bus,
                        class_name=excluded.class_name, metadata_json=excluded.metadata_json
                    """,
                    normalized,
                )
                count += 1
        return count

    @staticmethod
    def _component_items_from_json(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("components") or payload.get("nodes") or payload.get("items") or []
        else:
            items = []
        return [item for item in items if isinstance(item, dict)]

    @staticmethod
    def _first_string(item: dict[str, Any], *keys: str, default: str = "") -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return default

    def _normalize_component_item(self, item: dict[str, Any], fallback_rank: int) -> tuple[Any, ...]:
        slug = self._first_string(item, "id", "slug", "name", "model", default=f"component-{fallback_rank}").lower().replace(" ", "-")
        kind = self._first_string(item, "kind", "category", "type", default="component").lower()
        metadata = dict(item.get("metadata") or {})
        metadata.update({"source": "designer-antmicro-library", "slug": slug})
        return (
            f"{kind}:{slug}",
            kind,
            int(item.get("rank") or fallback_rank),
            self._first_string(item, "model", "name", "label", default=slug),
            self._first_string(item, "vendor", "manufacturer", "producer", default="Unknown"),
            self._first_string(item, "tier", "fidelity", "fidelity_tier", default="external"),
            self._first_string(item, "bus", "bus_type", "interface", default="unspecified"),
            self._first_string(item, "class", "class_name", "renode_class", "renodeClass", default=""),
            json.dumps(metadata, sort_keys=True),
        )

    def component_stats(self) -> dict[str, Any]:
        with self.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM components").fetchone()[0]
            by_kind = {
                row["kind"]: row["count"]
                for row in db.execute(
                    "SELECT kind, COUNT(*) AS count FROM components GROUP BY kind ORDER BY kind"
                )
            }
            by_tier = {
                row["tier"]: row["count"]
                for row in db.execute(
                    "SELECT tier, COUNT(*) AS count FROM components GROUP BY tier ORDER BY tier"
                )
            }
        return {"total": total, "by_kind": by_kind, "by_tier": by_tier}

    def search_components(
        self,
        query: str = "",
        *,
        kind: str | None = None,
        bus: str | None = None,
        tier: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ComponentRecord]:
        clauses: list[str] = []
        values: list[Any] = []
        if query:
            clauses.append("(id LIKE ? OR model LIKE ? OR vendor LIKE ? OR class_name LIKE ?)")
            token = f"%{query}%"
            values.extend([token, token, token, token])
        for column, value in (("kind", kind), ("bus", bus), ("tier", tier)):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        values.extend([max(1, min(limit, 1000)), max(offset, 0)])
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM components" + where + " ORDER BY kind, rank LIMIT ? OFFSET ?",
                values,
            ).fetchall()
        return [
            ComponentRecord(
                id=row["id"],
                kind=row["kind"],
                rank=row["rank"],
                model=row["model"],
                vendor=row["vendor"],
                tier=row["tier"],
                bus=row["bus"],
                class_name=row["class_name"],
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        ]

    def put_asset(
        self,
        source: BinaryIO | bytes | bytearray | str | Path,
        *,
        name: str | None = None,
        mime: str | None = None,
    ) -> AssetRecord:
        temp_path = self.root / f".asset-{uuid.uuid4().hex}.tmp"
        digest = hashlib.sha256()
        size = 0
        if isinstance(source, (str, Path)):
            input_handle: BinaryIO = Path(source).open("rb")
            close_input = True
            if name is None:
                name = Path(source).name
        elif isinstance(source, (bytes, bytearray)):
            from io import BytesIO
            input_handle = BytesIO(bytes(source))
            close_input = True
        else:
            input_handle = source
            close_input = False
        try:
            with temp_path.open("wb") as output:
                while True:
                    chunk = input_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    output.write(chunk)
                    size += len(chunk)
        finally:
            if close_input:
                input_handle.close()

        sha256 = digest.hexdigest()
        destination = self.assets_root / sha256[:2] / sha256[2:4] / sha256
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            temp_path.unlink(missing_ok=True)
        else:
            os.replace(temp_path, destination)
        asset_name = name or sha256
        asset_mime = mime or mimetypes.guess_type(asset_name)[0] or "application/octet-stream"
        created_at = time.time()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO assets(sha256, name, mime, size, stored_path, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(sha256) DO UPDATE SET name=excluded.name, mime=excluded.mime
                """,
                (sha256, asset_name, asset_mime, size, str(destination), created_at),
            )
            row = db.execute("SELECT * FROM assets WHERE sha256 = ?", (sha256,)).fetchone()
        return self._asset_from_row(row)

    def get_asset(self, asset_id: int) -> AssetRecord | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return self._asset_from_row(row) if row else None

    def list_assets(self, limit: int = 100) -> list[AssetRecord]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM assets ORDER BY created_at DESC LIMIT ?", (min(limit, 1000),)
            ).fetchall()
        return [self._asset_from_row(row) for row in rows]

    @staticmethod
    def _asset_from_row(row: sqlite3.Row) -> AssetRecord:
        return AssetRecord(
            id=row["id"],
            sha256=row["sha256"],
            name=row["name"],
            mime=row["mime"],
            size=row["size"],
            stored_path=Path(row["stored_path"]),
            created_at=row["created_at"],
        )

    def save_project(self, name: str, graph: dict[str, Any], project_id: str | None = None) -> str:
        project_id = project_id or uuid.uuid4().hex
        now = time.time()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO projects(id, name, graph_json, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, graph_json=excluded.graph_json, updated_at=excluded.updated_at
                """,
                (project_id, name, json.dumps(graph), now, now),
            )
        return project_id

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "graph": json.loads(row["graph_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "graph": json.loads(row["graph_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def delete_project(self, project_id: str) -> bool:
        with self.connect() as db:
            cursor = db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cursor.rowcount > 0

    def create_session(
        self,
        *,
        target: str,
        project_id: str | None = None,
        firmware_asset_id: int | None = None,
        command: Sequence[str] = (),
        metadata: dict[str, Any] | None = None,
    ) -> str:
        session_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO sessions(id, project_id, target, firmware_asset_id, status,
                                     command_json, metadata_json, started_at)
                VALUES(?, ?, ?, ?, 'starting', ?, ?, ?)
                """,
                (
                    session_id,
                    project_id,
                    target,
                    firmware_asset_id,
                    json.dumps(list(command)),
                    json.dumps(metadata or {}),
                    time.time(),
                ),
            )
        return session_id

    def update_session(self, session_id: str, *, status: str, stopped: bool = False) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE sessions SET status = ?, stopped_at = COALESCE(?, stopped_at) WHERE id = ?",
                (status, time.time() if stopped else None, session_id),
            )

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (min(limit, 1000),)
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return self._session_from_row(row) if row else None

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "target": row["target"],
            "firmware_asset_id": row["firmware_asset_id"],
            "status": row["status"],
            "command": json.loads(row["command_json"]),
            "metadata": json.loads(row["metadata_json"]),
            "started_at": row["started_at"],
            "stopped_at": row["stopped_at"],
        }

    def append_telemetry(self, session_id: str, points: Iterable[TelemetryPoint]) -> int:
        rows = []
        for point in points:
            if isinstance(point.value, (bool, int, float)):
                value_num = float(point.value)
                value_text = None
            else:
                value_num = None
                value_text = str(point.value)
            rows.append(
                (
                    session_id,
                    point.timestamp,
                    point.channel,
                    value_num,
                    value_text,
                    point.kind,
                    json.dumps(point.metadata),
                )
            )
        if not rows:
            return 0
        with self.connect() as db:
            db.executemany(
                """
                INSERT INTO telemetry(session_id, ts, channel, value_num, value_text, kind, metadata_json)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def query_telemetry(
        self,
        session_id: str,
        *,
        channels: Sequence[str] | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        clauses = ["session_id = ?"]
        values: list[Any] = [session_id]
        if channels:
            placeholders = ",".join("?" for _ in channels)
            clauses.append(f"channel IN ({placeholders})")
            values.extend(channels)
        if since is not None:
            clauses.append("ts >= ?")
            values.append(since)
        if until is not None:
            clauses.append("ts <= ?")
            values.append(until)
        values.append(min(max(limit, 1), 200000))
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM telemetry WHERE " + " AND ".join(clauses) + " ORDER BY ts LIMIT ?",
                values,
            ).fetchall()
        return [
            {
                "timestamp": row["ts"],
                "channel": row["channel"],
                "value": row["value_num"] if row["value_num"] is not None else row["value_text"],
                "kind": row["kind"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def append_log(
        self,
        *,
        message: str,
        session_id: str | None = None,
        level: str = "INFO",
        source: str = "vsd",
        timestamp: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO logs(session_id, ts, level, source, message, metadata_json)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    timestamp or time.time(),
                    level.upper(),
                    source,
                    message,
                    json.dumps(metadata or {}),
                ),
            )

    def query_logs(
        self,
        *,
        session_id: str | None = None,
        level: str | None = None,
        source: str | None = None,
        search: str | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        for column, value in (("session_id", session_id), ("level", level), ("source", source)):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value.upper() if column == "level" else value)
        if search:
            clauses.append("message LIKE ?")
            values.append(f"%{search}%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        values.append(min(max(limit, 1), 100000))
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM logs" + where + " ORDER BY ts DESC LIMIT ?", values
            ).fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "timestamp": row["ts"],
                "level": row["level"],
                "source": row["source"],
                "message": row["message"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def save_analysis(self, asset_id: int, architecture: str | None, report: dict[str, Any]) -> str:
        analysis_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO analyses(id, asset_id, architecture, report_json, created_at) VALUES(?, ?, ?, ?, ?)",
                (analysis_id, asset_id, architecture, json.dumps(report), time.time()),
            )
        return analysis_id

    def vacuum(self) -> None:
        with self.connect() as db:
            db.execute("VACUUM")
