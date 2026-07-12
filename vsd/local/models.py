from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AssetRecord:
    id: int
    sha256: str
    name: str
    mime: str
    size: int
    stored_path: Path
    created_at: float


@dataclass(slots=True)
class ComponentRecord:
    id: str
    kind: str
    rank: int
    model: str
    vendor: str
    tier: str
    bus: str
    class_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TelemetryPoint:
    timestamp: float
    channel: str
    value: float | int | bool | str
    kind: str = "analog"
    metadata: dict[str, Any] = field(default_factory=dict)
