import pytest

from vsd.local.store import LocalStore
from vsd.local.telemetry import TelemetryHub, parse_telemetry_line


def test_parse_telemetry_lines():
    assert parse_telemetry_line('VSD:temperature=24.5').value == 24.5
    assert parse_telemetry_line('VSD_TELEMETRY {"channel":"gpio","value":true,"kind":"digital"}').kind == "digital"
    assert parse_telemetry_line("plain log") is None


@pytest.mark.asyncio
async def test_hub_persists_and_streams(tmp_path):
    store = LocalStore(tmp_path)
    session = store.create_session(target="test", command=["test"])
    hub = TelemetryHub(store)
    queue = await hub.subscribe(session)
    point = parse_telemetry_line("VSD:adc=1.25")
    assert await hub.publish(session, [point]) == 1
    streamed = await queue.get()
    assert streamed["channel"] == "adc"
    stored = store.query_telemetry(session)
    assert stored[0]["value"] == 1.25
