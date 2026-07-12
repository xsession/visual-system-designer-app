from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any

from .models import TelemetryPoint
from .store import LocalStore


class TelemetryHub:
    def __init__(self, store: LocalStore, queue_size: int = 4096):
        self.store = store
        self.queue_size = queue_size
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, session_id: str, points: list[TelemetryPoint]) -> int:
        count = await asyncio.to_thread(self.store.append_telemetry, session_id, points)
        payloads = [
            {
                "timestamp": point.timestamp,
                "channel": point.channel,
                "value": point.value,
                "kind": point.kind,
                "metadata": point.metadata,
            }
            for point in points
        ]
        async with self._lock:
            subscribers = tuple(self._subscribers.get(session_id, ()))
        for queue in subscribers:
            for payload in payloads:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(payload)
        return count

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.queue_size)
        async with self._lock:
            self._subscribers[session_id].add(queue)
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(session_id)
            if not subscribers:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(session_id, None)


def parse_telemetry_line(line: str, timestamp: float | None = None) -> TelemetryPoint | None:
    """Parse firmware/emulator telemetry without coupling to a specific target.

    Accepted formats:
      VSD_TELEMETRY {"channel":"temperature","value":21.5,"kind":"analog"}
      VSD:temperature=21.5
      VSD:gpio.4=true
    """
    import json

    ts = timestamp or time.time()
    stripped = line.strip()
    if stripped.startswith("VSD_TELEMETRY "):
        try:
            payload = json.loads(stripped[len("VSD_TELEMETRY "):])
            return TelemetryPoint(
                timestamp=float(payload.get("timestamp", ts)),
                channel=str(payload["channel"]),
                value=payload["value"],
                kind=str(payload.get("kind", "analog")),
                metadata=dict(payload.get("metadata", {})),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
    if stripped.startswith("VSD:") and "=" in stripped:
        name, raw_value = stripped[4:].split("=", 1)
        value: Any
        normalized = raw_value.strip().lower()
        if normalized in {"true", "high", "on"}:
            value = True
        elif normalized in {"false", "low", "off"}:
            value = False
        else:
            try:
                value = float(raw_value)
            except ValueError:
                value = raw_value
        return TelemetryPoint(timestamp=ts, channel=name.strip(), value=value, kind="logic" if isinstance(value, bool) else "analog")
    return None
