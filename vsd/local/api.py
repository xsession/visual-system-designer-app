from __future__ import annotations

import asyncio
import json
import time
from importlib.resources import files
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import LocalConfig
from .emulation import EmulationManager
from .filtering import UnsafeFilterExpression, apply_filter, apply_pipeline, build_plot_data
from .firmware import FirmwareAnalyzer
from .models import TelemetryPoint
from .store import LocalStore
from .telemetry import TelemetryHub


class ProjectPayload(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    graph: dict[str, Any]


class FirmwareAnalyzePayload(BaseModel):
    asset_id: int
    architecture: str | None = None


class SessionStartPayload(BaseModel):
    target: str = "renode"
    project_id: str | None = None
    firmware_asset_id: int | None = None
    platform_asset_id: int | None = None
    script_asset_id: int | None = None
    extra_args: list[str] = Field(default_factory=list)
    custom_command: list[str] | None = None


class TelemetryPayload(BaseModel):
    timestamp: float | None = None
    channel: str
    value: float | int | bool | str
    kind: str = "analog"
    metadata: dict[str, Any] = Field(default_factory=dict)


class TelemetryBatchPayload(BaseModel):
    points: list[TelemetryPayload]


class FilterPipelinePayload(BaseModel):
    expression: str = ""
    pipeline: list[dict[str, Any]] = Field(default_factory=list)


def create_app(config: LocalConfig | None = None) -> FastAPI:
    config = config or LocalConfig.from_env()
    store = LocalStore(config.data_dir)
    catalog_path = files("vsd.local.assets").joinpath("components.csv")
    store.import_component_catalog(catalog_path)
    telemetry = TelemetryHub(store)
    emulation = EmulationManager(store, telemetry)
    analyzer = FirmwareAnalyzer()

    app = FastAPI(
        title="Visual System Designer Local Studio",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.config = config
    app.state.store = store
    app.state.telemetry = telemetry
    app.state.emulation = emulation
    app.state.analyzer = analyzer

    static_dir = files("vsd.local.static")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    async def require_token(authorization: str | None = Header(default=None)) -> None:
        if not config.api_token:
            return
        if authorization != f"Bearer {config.api_token}":
            raise HTTPException(status_code=401, detail="A valid local studio token is required")

    @app.on_event("shutdown")
    async def stop_processes() -> None:
        await emulation.stop_all()

    @app.get("/", include_in_schema=False)
    async def root() -> FileResponse:
        return FileResponse(static_dir.joinpath("index.html"))

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "local_only": not config.allow_remote,
            "data_dir": str(config.data_dir),
            "workspace": str(config.workspace),
            "catalog": store.component_stats(),
            "toolchain": emulation.toolchain.as_dict(),
        }

    @app.get("/api/components")
    async def components(
        query: str = "",
        kind: str | None = None,
        bus: str | None = None,
        tier: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict[str, Any]]:
        return [asdict(component) for component in store.search_components(
            query, kind=kind, bus=bus, tier=tier, limit=limit, offset=offset
        )]

    @app.get("/api/components/stats")
    async def component_stats() -> dict[str, Any]:
        return store.component_stats()

    @app.get("/api/bundled-assets")
    async def bundled_assets() -> list[dict[str, Any]]:
        package = files("vsd.local.assets")
        names = ("components.csv", "renode-external-components-overlay.zip")
        return [
            {"name": name, "size": len(package.joinpath(name).read_bytes())}
            for name in names
        ]

    @app.get("/api/bundled-assets/{name}")
    async def bundled_asset(name: str) -> Response:
        allowed = {
            "components.csv": "text/csv",
            "renode-external-components-overlay.zip": "application/zip",
        }
        if name not in allowed:
            raise HTTPException(status_code=404, detail="Bundled asset not found")
        content = files("vsd.local.assets").joinpath(name).read_bytes()
        return Response(
            content=content,
            media_type=allowed[name],
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.post("/api/assets", dependencies=[Depends(require_token)])
    async def upload_asset(file: UploadFile = File(...)) -> dict[str, Any]:
        record = await asyncio.to_thread(
            store.put_asset,
            file.file,
            name=file.filename or "asset.bin",
            mime=file.content_type,
        )
        return _asset_json(record)

    @app.get("/api/assets")
    async def assets(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
        return [_asset_json(record) for record in store.list_assets(limit)]

    @app.get("/api/assets/{asset_id}")
    async def download_asset(asset_id: int) -> FileResponse:
        record = store.get_asset(asset_id)
        if not record or not record.stored_path.exists():
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(record.stored_path, filename=record.name, media_type=record.mime)

    @app.get("/api/projects")
    async def projects() -> list[dict[str, Any]]:
        return store.list_projects()

    @app.get("/api/projects/{project_id}")
    async def project(project_id: str) -> dict[str, Any]:
        record = store.get_project(project_id)
        if not record:
            raise HTTPException(status_code=404, detail="Project not found")
        return record

    @app.post("/api/projects", dependencies=[Depends(require_token)])
    async def save_project(payload: ProjectPayload) -> dict[str, Any]:
        project_id = store.save_project(payload.name, payload.graph, payload.id)
        return store.get_project(project_id) or {"id": project_id}

    @app.delete("/api/projects/{project_id}", dependencies=[Depends(require_token)])
    async def delete_project(project_id: str) -> dict[str, bool]:
        return {"deleted": store.delete_project(project_id)}

    @app.post("/api/firmware/analyze", dependencies=[Depends(require_token)])
    async def analyze_firmware(payload: FirmwareAnalyzePayload) -> dict[str, Any]:
        asset = store.get_asset(payload.asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Firmware asset not found")
        report = await asyncio.to_thread(analyzer.analyze, asset.stored_path, payload.architecture)
        analysis_id = store.save_analysis(asset.id, payload.architecture, report)
        return {"id": analysis_id, "asset_id": asset.id, "report": report}

    @app.get("/api/toolchain")
    async def toolchain() -> dict[str, str | None]:
        return emulation.toolchain.as_dict()

    @app.get("/api/sessions")
    async def sessions(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
        return store.list_sessions(limit)

    @app.post("/api/sessions", dependencies=[Depends(require_token)])
    async def start_session(payload: SessionStartPayload) -> dict[str, Any]:
        firmware = _asset_path(store, payload.firmware_asset_id)
        platform = _asset_path(store, payload.platform_asset_id)
        script = _asset_path(store, payload.script_asset_id)
        try:
            session_id = await emulation.start(
                target=payload.target,
                firmware=firmware,
                platform=platform,
                script=script,
                project_id=payload.project_id,
                firmware_asset_id=payload.firmware_asset_id,
                extra_args=payload.extra_args,
                custom_command=payload.custom_command,
            )
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return store.get_session(session_id) or {"id": session_id}

    @app.post("/api/sessions/{session_id}/stop", dependencies=[Depends(require_token)])
    async def stop_session(session_id: str) -> dict[str, bool]:
        return {"stopped": await emulation.stop(session_id)}

    @app.post("/api/sessions/{session_id}/telemetry", dependencies=[Depends(require_token)])
    async def ingest_telemetry(session_id: str, payload: TelemetryBatchPayload) -> dict[str, int]:
        if not store.get_session(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        now = time.time()
        points = [
            TelemetryPoint(
                timestamp=point.timestamp or now,
                channel=point.channel,
                value=point.value,
                kind=point.kind,
                metadata=point.metadata,
            )
            for point in payload.points
        ]
        return {"inserted": await telemetry.publish(session_id, points)}

    @app.get("/api/sessions/{session_id}/telemetry")
    async def query_telemetry(
        session_id: str,
        channel: list[str] = Query(default=[]),
        since: float | None = None,
        until: float | None = None,
        limit: int = Query(default=10000, ge=1, le=200000),
        expression: str = "",
        pipeline: str = "[]",
        plot: str = "time",
        bins: int = Query(default=64, ge=2, le=2048),
    ) -> dict[str, Any]:
        points = store.query_telemetry(
            session_id, channels=channel or None, since=since, until=until, limit=limit
        )
        try:
            filtered = apply_filter(points, expression)
            stages = json.loads(pipeline)
            if not isinstance(stages, list):
                raise ValueError("pipeline must be a JSON list")
            filtered = apply_pipeline(filtered, stages)
            return {"points": filtered, "plot": build_plot_data(filtered, plot, bins=bins)}
        except (UnsafeFilterExpression, ValueError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/logs")
    async def logs(
        session_id: str | None = None,
        level: str | None = None,
        source: str | None = None,
        search: str | None = None,
        limit: int = Query(default=5000, ge=1, le=100000),
    ) -> list[dict[str, Any]]:
        return store.query_logs(
            session_id=session_id, level=level, source=source, search=search, limit=limit
        )

    @app.websocket("/ws/telemetry/{session_id}")
    async def telemetry_socket(websocket: WebSocket, session_id: str) -> None:
        if config.api_token:
            token = websocket.query_params.get("token")
            if token != config.api_token:
                await websocket.close(code=4401)
                return
        await websocket.accept()
        queue = await telemetry.subscribe(session_id)
        try:
            while True:
                point = await queue.get()
                await websocket.send_json(point)
        except WebSocketDisconnect:
            pass
        finally:
            await telemetry.unsubscribe(session_id, queue)

    return app


def _asset_json(record) -> dict[str, Any]:
    return {
        "id": record.id,
        "sha256": record.sha256,
        "name": record.name,
        "mime": record.mime,
        "size": record.size,
        "created_at": record.created_at,
    }


def _asset_path(store: LocalStore, asset_id: int | None) -> Path | None:
    if asset_id is None:
        return None
    record = store.get_asset(asset_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
    return record.stored_path
