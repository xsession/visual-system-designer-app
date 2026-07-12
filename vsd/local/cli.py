from __future__ import annotations

import json
from dataclasses import asdict
import os
import sys
import webbrowser
from pathlib import Path
from threading import Timer
from typing import Optional

import typer

from .config import LocalConfig
from .firmware import FirmwareAnalyzer
from .store import LocalStore

studio_app = typer.Typer(
    name="studio",
    help="Local-first component design, bare-metal emulation and observability studio.",
    no_args_is_help=True,
)


@studio_app.command("run")
def run_studio(
    data_dir: Optional[Path] = typer.Option(None, help="Local database and content-addressed asset directory."),
    workspace: Optional[Path] = typer.Option(None, help="VSD/firmware workspace exposed to the studio."),
    host: str = typer.Option("127.0.0.1", help="Bind address. Remote binds require a bearer token."),
    port: int = typer.Option(9010, min=1, max=65535),
    open_browser: bool = typer.Option(True, "--open-browser/--no-open-browser"),
    allow_remote: bool = typer.Option(False, help="Allow non-loopback binding and generate a local token."),
) -> None:
    """Run the local Studio web UI and API."""
    try:
        import uvicorn
        from .api import create_app
    except ImportError as error:
        raise typer.BadParameter(
            "Studio dependencies are missing. Install with `pip install -e '.[studio]'` "
            "or use packaging/requirements-studio.txt."
        ) from error

    config = LocalConfig.from_env(
        data_dir=data_dir,
        workspace=workspace,
        host=host,
        port=port,
        allow_remote=allow_remote,
    )
    if config.allow_remote and config.api_token:
        typer.echo(f"Remote API token: {config.api_token}")
    url = f"http://{host if host not in {'0.0.0.0', '::'} else '127.0.0.1'}:{port}"
    if open_browser:
        Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(config), host=config.host, port=config.port, log_level="info")


@studio_app.command("doctor")
def doctor(
    data_dir: Optional[Path] = typer.Option(None),
    workspace: Optional[Path] = typer.Option(None),
) -> None:
    """Check the local database, catalog and reverse-engineering toolchain."""
    from .emulation import ToolchainStatus
    from importlib.resources import files

    config = LocalConfig.from_env(data_dir=data_dir, workspace=workspace)
    store = LocalStore(config.data_dir)
    imported = store.import_component_catalog(files("vsd.local.assets").joinpath("components.csv"))
    payload = {
        "data_dir": str(config.data_dir),
        "workspace": str(config.workspace),
        "database": str(store.db_path),
        "catalog_imported": imported,
        "catalog_stats": store.component_stats(),
        "toolchain": ToolchainStatus.discover().as_dict(),
    }
    typer.echo(json.dumps(payload, indent=2))


@studio_app.command("analyze")
def analyze(
    firmware: Path = typer.Argument(..., exists=True, dir_okay=False),
    architecture: Optional[str] = typer.Option(None),
    output: Optional[Path] = typer.Option(None),
) -> None:
    """Analyze ELF/HEX/UF2/raw firmware locally without uploading it."""
    report = FirmwareAnalyzer().analyze(firmware, architecture)
    encoded = json.dumps(report, indent=2)
    if output:
        output.write_text(encoded + "\n", encoding="utf-8")
        typer.echo(str(output))
    else:
        typer.echo(encoded)


@studio_app.command("catalog")
def catalog(
    query: str = typer.Argument(""),
    kind: Optional[str] = typer.Option(None),
    bus: Optional[str] = typer.Option(None),
    limit: int = typer.Option(50, min=1, max=1000),
    data_dir: Optional[Path] = typer.Option(None),
) -> None:
    """Search the bundled 500-device component catalog."""
    from importlib.resources import files

    config = LocalConfig.from_env(data_dir=data_dir)
    store = LocalStore(config.data_dir)
    store.import_component_catalog(files("vsd.local.assets").joinpath("components.csv"))
    records = store.search_components(query, kind=kind, bus=bus, limit=limit)
    typer.echo(json.dumps([asdict(record) for record in records], indent=2))


def main() -> None:
    studio_app()


if __name__ == "__main__":
    main()
