from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class LibraryConfig:
    root: Path
    offline: bool = True

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def store_dir(self) -> Path:
        return self.root / "store"

    @property
    def objects_dir(self) -> Path:
        return self.store_dir / "objects"

    @property
    def database_path(self) -> Path:
        return self.store_dir / "library.sqlite3"

    @property
    def generated_dir(self) -> Path:
        return self.root / "generated"

    @property
    def config_path(self) -> Path:
        return self.root / "library.yml"


def default_library_root(workspace: Path | None = None) -> Path:
    if os.environ.get("VSD_LIBRARY_DIR"):
        return Path(os.environ["VSD_LIBRARY_DIR"]).expanduser()
    if workspace:
        return workspace / "library"
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "visual-system-designer" / "library"


def load_config(root: Path) -> LibraryConfig:
    config = LibraryConfig(root.expanduser().resolve())
    if config.config_path.exists():
        data: dict[str, Any] = yaml.safe_load(config.config_path.read_text(encoding="utf-8")) or {}
        config.offline = bool(data.get("offline", True))
    return config


def write_default_config(config: LibraryConfig) -> None:
    config.root.mkdir(parents=True, exist_ok=True)
    text = """version: 1
offline: true
database:
  path: store/library.sqlite3
asset_store:
  path: store/objects
  algorithm: sha256
sources:
  - id: legacy-vsd
    type: vsd-resources
    path: sources/visual-system-designer-resources
    priority: 10
  - id: designer-media
    type: media-directory
    path: sources/designer-media-files
    priority: 20
  - id: hardware-components
    type: antmicro-hardware-components
    path: sources/hardware-components
    priority: 30
  - id: designer-graphs
    type: pipeline-manager-graphs
    path: sources/designer-graphs
    priority: 40
  - id: local
    type: local-overrides
    path: sources/custom
    priority: 100
generated:
  directory: generated
"""
    config.config_path.write_text(text, encoding="utf-8")

