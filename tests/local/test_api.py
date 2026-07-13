import json

from fastapi.testclient import TestClient

from vsd.local.api import create_app
from vsd.local.config import LocalConfig


def test_api_catalog_project_telemetry(tmp_path):
    config = LocalConfig.from_env(data_dir=tmp_path / "data", workspace=tmp_path / "workspace")
    with TestClient(create_app(config)) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["catalog"]["total"] == 500

        bundled = client.get("/api/bundled-assets")
        assert len(bundled.json()) == 2
        assert client.get("/api/bundled-assets/renode-external-components-overlay.zip").status_code == 200

        result = client.get("/api/components", params={"query": "bme280"})
        assert result.status_code == 200
        assert result.json()

        library = {
            "components": [
                {
                    "id": "pic32mz-ef",
                    "kind": "controller",
                    "model": "PIC32MZ EF",
                    "vendor": "Microchip",
                    "bus": "wishbone",
                    "renode_class": "Antmicro.Renode.Peripherals.CPU.PIC32MZ",
                }
            ]
        }
        imported = client.post(
            "/api/components/import",
            files={"file": ("library.json", json.dumps(library), "application/json")},
        )
        assert imported.status_code == 200
        assert imported.json()["imported"] == 1

        project = client.post("/api/projects", json={"name": "board", "graph": {"nodes": [], "edges": []}})
        assert project.status_code == 200
        project_id = project.json()["id"]
        assert client.get(f"/api/projects/{project_id}").status_code == 200

        session_id = config.data_dir.joinpath("dummy")
        store = client.app.state.store
        sid = store.create_session(target="test", command=["test"])
        ingest = client.post(
            f"/api/sessions/{sid}/telemetry",
            json={"points": [{"channel": "signal", "value": 3.0}]},
        )
        assert ingest.json()["inserted"] == 1
        plot = client.get(f"/api/sessions/{sid}/telemetry", params={"plot": "histogram"})
        assert plot.status_code == 200
        assert plot.json()["plot"]["mode"] == "histogram"
