from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

import typer

from .config import LibraryConfig, default_library_root, load_config, write_default_config
from .database import LibraryDatabase
from .importers import import_graphs, import_hardware_components, import_media, import_vsd_resources


library_app = typer.Typer(name="library", help="Manage the local VSD component library.", no_args_is_help=True)


def _config(root: Optional[Path]) -> LibraryConfig:
    return load_config(root or default_library_root(Path("workspace")))


@library_app.command("init")
def init(root: Optional[Path] = typer.Option(None, help="Library root. Defaults to VSD_LIBRARY_DIR or workspace/library.")) -> None:
    config = _config(root)
    for directory in (config.sources_dir, config.store_dir, config.objects_dir, config.generated_dir, config.root / "manifests", config.root / "licenses"):
        directory.mkdir(parents=True, exist_ok=True)
    if not config.config_path.exists():
        write_default_config(config)
    LibraryDatabase(config.database_path)
    typer.echo(str(config.root))


@library_app.command("source-list")
def source_list(root: Optional[Path] = typer.Option(None)) -> None:
    config = _config(root)
    sources = [
        "visual-system-designer-resources",
        "designer-media-files",
        "hardware-components",
        "designer-graphs",
        "custom",
    ]
    typer.echo(json.dumps({name: str(config.sources_dir / name) for name in sources}, indent=2))


@library_app.command("sync")
def sync(root: Optional[Path] = typer.Option(None), apply: bool = typer.Option(False, "--apply", help="Run git clone/pull. Preview by default.")) -> None:
    config = _config(root)
    repos = {
        "visual-system-designer-resources": "https://github.com/antmicro/visual-system-designer-resources.git",
        "designer-media-files": "https://github.com/antmicro/designer-media-files.git",
        "hardware-components": "https://github.com/antmicro/hardware-components.git",
        "designer-graphs": "https://github.com/antmicro/designer-graphs.git",
    }
    config.sources_dir.mkdir(parents=True, exist_ok=True)
    for name, url in repos.items():
        target = config.sources_dir / name
        if not apply or config.offline:
            typer.echo(f"PREVIEW: {'update' if target.exists() else 'clone'} {url} -> {target}")
            continue
        if target.exists():
            subprocess.run(["git", "-C", str(target), "pull", "--ff-only"], check=True)
        else:
            subprocess.run(["git", "clone", "--depth", "1", url, str(target)], check=True)


@library_app.command("import")
def import_sources(root: Optional[Path] = typer.Option(None)) -> None:
    config = _config(root)
    db = LibraryDatabase(config.database_path)
    counts = {
        "vsd_resources": import_vsd_resources(db, config.sources_dir / "visual-system-designer-resources"),
        "hardware_components": import_hardware_components(db, config.sources_dir / "hardware-components"),
        "media_assets": import_media(db, config, config.sources_dir / "designer-media-files"),
        "graphs": import_graphs(db, config.sources_dir / "designer-graphs"),
    }
    typer.echo(json.dumps({"imported": counts, "stats": db.stats()}, indent=2))


@library_app.command("validate")
def validate(root: Optional[Path] = typer.Option(None)) -> None:
    config = _config(root)
    missing = [str(path) for path in (config.config_path, config.database_path) if not path.exists()]
    if missing:
        raise typer.BadParameter("Missing library files: " + ", ".join(missing))
    typer.echo(json.dumps(LibraryDatabase(config.database_path).stats(), indent=2))


@library_app.command("build")
def build(root: Optional[Path] = typer.Option(None)) -> None:
    config = _config(root)
    config.generated_dir.mkdir(parents=True, exist_ok=True)
    db = LibraryDatabase(config.database_path)
    entities = db.search("", limit=100000)
    specification = {"nodes": [{"id": item["id"], "name": item["name"], "category": item["category"], "metadata": item["metadata"]} for item in entities]}
    (config.generated_dir / "components-specification.json").write_text(json.dumps(specification, indent=2), encoding="utf-8")
    typer.echo(str(config.generated_dir / "components-specification.json"))


@library_app.command("search")
def search(query: str, root: Optional[Path] = typer.Option(None), limit: int = typer.Option(20, min=1, max=1000)) -> None:
    config = _config(root)
    typer.echo(json.dumps(LibraryDatabase(config.database_path).search(query, limit), indent=2))

