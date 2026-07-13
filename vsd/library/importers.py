from __future__ import annotations

import json
from pathlib import Path

from .assets import ingest_asset
from .config import LibraryConfig
from .database import LibraryDatabase


def import_vsd_resources(db: LibraryDatabase, root: Path, source_id: str = "legacy-vsd") -> int:
    specification = root / "components-specification.json"
    if not specification.exists():
        return 0
    payload = json.loads(specification.read_text(encoding="utf-8"))
    nodes = payload.get("nodes") or payload.get("components") or []
    count = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = node.get("name") or node.get("label") or node.get("id")
        if not name:
            continue
        db.upsert_entity(
            entity_id=f"{source_id}:{node.get('id', name)}",
            entity_type="component",
            name=name,
            vendor=node.get("vendor"),
            category=node.get("category"),
            description=node.get("description"),
            source_id=source_id,
            metadata=node,
        )
        count += 1
    return count


def import_hardware_components(db: LibraryDatabase, root: Path, source_id: str = "hardware-components") -> int:
    count = 0
    for path in (root / "components").rglob("*.json") if (root / "components").exists() else []:
        payload = json.loads(path.read_text(encoding="utf-8"))
        name = payload.get("name") or payload.get("mpn") or path.stem
        vendor = payload.get("manufacturer") or payload.get("vendor")
        db.upsert_entity(
            entity_id=f"{source_id}:{path.relative_to(root).as_posix()}",
            entity_type="hardware-component",
            name=name,
            vendor=vendor,
            category=payload.get("category"),
            description=payload.get("description"),
            source_id=source_id,
            metadata=payload,
        )
        count += 1
    return count


def import_media(db: LibraryDatabase, config: LibraryConfig, root: Path, source_id: str = "designer-media") -> int:
    if not root.exists():
        return 0
    count = 0
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".webp", ".webm", ".png", ".jpg", ".jpeg", ".svg", ".gltf", ".glb", ".blend"}:
            ingest_asset(db, path, config.objects_dir, source_id)
            count += 1
    return count


def import_graphs(db: LibraryDatabase, root: Path, source_id: str = "designer-graphs") -> int:
    if not root.exists():
        return 0
    count = 0
    with db.connect() as connection:
        for path in root.rglob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            graph_id = f"{source_id}:{path.relative_to(root).as_posix()}"
            connection.execute(
                """
                INSERT INTO graphs(id, name, graph_json, source_id)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, graph_json=excluded.graph_json
                """,
                (graph_id, payload.get("name") or path.stem, json.dumps(payload, sort_keys=True), source_id),
            )
            count += 1
    return count

