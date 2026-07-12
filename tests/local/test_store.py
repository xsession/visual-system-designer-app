from importlib.resources import files
from io import BytesIO

from vsd.local.store import LocalStore


def test_catalog_assets_and_projects(tmp_path):
    store = LocalStore(tmp_path)
    imported = store.import_component_catalog(files("vsd.local.assets").joinpath("components.csv"))
    assert imported == 500
    assert store.component_stats()["total"] == 500
    assert len(store.search_components("ssd1306")) >= 1

    first = store.put_asset(BytesIO(b"firmware"), name="fw.bin")
    second = store.put_asset(BytesIO(b"firmware"), name="copy.bin")
    assert first.id == second.id
    assert first.stored_path.read_bytes() == b"firmware"

    project_id = store.save_project("demo", {"nodes": [{"id": "n1"}], "edges": []})
    project = store.get_project(project_id)
    assert project["name"] == "demo"
    assert project["graph"]["nodes"][0]["id"] == "n1"
