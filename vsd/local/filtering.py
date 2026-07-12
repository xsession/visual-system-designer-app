from __future__ import annotations

import ast
import math
from collections import defaultdict, deque
from typing import Any, Iterable

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


_ALLOWED_NODES = {
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
}
_ALLOWED_NAMES = {"value", "channel", "kind", "timestamp"}


class UnsafeFilterExpression(ValueError):
    pass


def compile_filter(expression: str):
    if not expression.strip():
        return lambda point: True
    tree = ast.parse(expression, mode="eval")
    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_NODES:
            raise UnsafeFilterExpression(f"Unsupported expression element: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
            raise UnsafeFilterExpression(f"Unknown filter name: {node.id}")
    code = compile(tree, "<telemetry-filter>", "eval")

    def predicate(point: dict[str, Any]) -> bool:
        scope = {name: point.get(name) for name in _ALLOWED_NAMES}
        return bool(eval(code, {"__builtins__": {}}, scope))

    return predicate


def apply_filter(points: Iterable[dict[str, Any]], expression: str = "") -> list[dict[str, Any]]:
    predicate = compile_filter(expression)
    return [point for point in points if predicate(point)]


def apply_pipeline(points: list[dict[str, Any]], pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = list(points)
    for stage in pipeline:
        operation = stage.get("op", "").lower()
        if operation == "moving_average":
            result = _moving_average(result, int(stage.get("window", 5)))
        elif operation == "ema":
            result = _ema(result, float(stage.get("alpha", 0.2)))
        elif operation == "lowpass":
            result = _lowpass(result, float(stage.get("cutoff_hz", 10.0)))
        elif operation == "highpass":
            result = _highpass(result, float(stage.get("cutoff_hz", 1.0)))
        elif operation == "derivative":
            result = _derivative(result)
        elif operation == "integral":
            result = _integral(result)
        elif operation == "downsample":
            result = result[:: max(1, int(stage.get("factor", 2)))]
        elif operation == "clamp":
            result = _clamp(result, stage.get("min"), stage.get("max"))
        elif operation == "normalize":
            result = _normalize(result)
        elif operation:
            raise ValueError(f"Unknown filter pipeline operation: {operation}")
    return result


def group_by_channel(points: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for point in points:
        grouped[str(point["channel"])].append(point)
    for channel_points in grouped.values():
        channel_points.sort(key=lambda item: float(item["timestamp"]))
    return dict(grouped)


def _numeric(point: dict[str, Any]) -> float | None:
    value = point.get("value")
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _moving_average(points: list[dict[str, Any]], window: int) -> list[dict[str, Any]]:
    window = max(1, window)
    output: list[dict[str, Any]] = []
    for _, channel_points in group_by_channel(points).items():
        values: deque[float] = deque(maxlen=window)
        for point in channel_points:
            value = _numeric(point)
            if value is None:
                continue
            values.append(value)
            output.append({**point, "value": sum(values) / len(values)})
    return sorted(output, key=lambda item: item["timestamp"])


def _ema(points: list[dict[str, Any]], alpha: float) -> list[dict[str, Any]]:
    alpha = max(0.0, min(1.0, alpha))
    output: list[dict[str, Any]] = []
    for _, channel_points in group_by_channel(points).items():
        state: float | None = None
        for point in channel_points:
            value = _numeric(point)
            if value is None:
                continue
            state = value if state is None else alpha * value + (1.0 - alpha) * state
            output.append({**point, "value": state})
    return sorted(output, key=lambda item: item["timestamp"])


def _estimate_dt(channel_points: list[dict[str, Any]]) -> float:
    deltas = [
        float(channel_points[index]["timestamp"]) - float(channel_points[index - 1]["timestamp"])
        for index in range(1, len(channel_points))
    ]
    positive = [delta for delta in deltas if delta > 0]
    return sorted(positive)[len(positive) // 2] if positive else 1.0


def _lowpass(points: list[dict[str, Any]], cutoff_hz: float) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    cutoff_hz = max(cutoff_hz, 1e-9)
    for _, channel_points in group_by_channel(points).items():
        if not channel_points:
            continue
        dt = _estimate_dt(channel_points)
        rc = 1.0 / (2.0 * math.pi * cutoff_hz)
        alpha = dt / (rc + dt)
        state: float | None = None
        for point in channel_points:
            value = _numeric(point)
            if value is None:
                continue
            state = value if state is None else state + alpha * (value - state)
            output.append({**point, "value": state})
    return sorted(output, key=lambda item: item["timestamp"])


def _highpass(points: list[dict[str, Any]], cutoff_hz: float) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    cutoff_hz = max(cutoff_hz, 1e-9)
    for _, channel_points in group_by_channel(points).items():
        if not channel_points:
            continue
        dt = _estimate_dt(channel_points)
        rc = 1.0 / (2.0 * math.pi * cutoff_hz)
        alpha = rc / (rc + dt)
        previous_input: float | None = None
        previous_output = 0.0
        for point in channel_points:
            value = _numeric(point)
            if value is None:
                continue
            current = 0.0 if previous_input is None else alpha * (previous_output + value - previous_input)
            previous_input = value
            previous_output = current
            output.append({**point, "value": current})
    return sorted(output, key=lambda item: item["timestamp"])


def _derivative(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for _, channel_points in group_by_channel(points).items():
        previous: dict[str, Any] | None = None
        for point in channel_points:
            value = _numeric(point)
            if value is None:
                continue
            if previous is not None:
                previous_value = _numeric(previous)
                dt = float(point["timestamp"]) - float(previous["timestamp"])
                if previous_value is not None and dt > 0:
                    output.append({**point, "value": (value - previous_value) / dt})
            previous = point
    return sorted(output, key=lambda item: item["timestamp"])


def _integral(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for _, channel_points in group_by_channel(points).items():
        previous: dict[str, Any] | None = None
        total = 0.0
        for point in channel_points:
            value = _numeric(point)
            if value is None:
                continue
            if previous is not None:
                previous_value = _numeric(previous)
                dt = float(point["timestamp"]) - float(previous["timestamp"])
                if previous_value is not None and dt > 0:
                    total += 0.5 * (value + previous_value) * dt
            output.append({**point, "value": total})
            previous = point
    return sorted(output, key=lambda item: item["timestamp"])


def _clamp(points: list[dict[str, Any]], minimum: Any, maximum: Any) -> list[dict[str, Any]]:
    low = float(minimum) if minimum is not None else -math.inf
    high = float(maximum) if maximum is not None else math.inf
    output = []
    for point in points:
        value = _numeric(point)
        output.append({**point, "value": max(low, min(high, value))} if value is not None else point)
    return output


def _normalize(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for _, channel_points in group_by_channel(points).items():
        numeric = [value for value in map(_numeric, channel_points) if value is not None]
        if not numeric:
            continue
        low, high = min(numeric), max(numeric)
        span = high - low
        for point in channel_points:
            value = _numeric(point)
            if value is not None:
                output.append({**point, "value": 0.0 if span == 0 else (value - low) / span})
    return sorted(output, key=lambda item: item["timestamp"])


def build_plot_data(points: list[dict[str, Any]], mode: str, bins: int = 64) -> dict[str, Any]:
    mode = mode.lower()
    grouped = group_by_channel(points)
    if mode in {"time", "scope"}:
        return {
            "mode": mode,
            "series": [
                {
                    "channel": channel,
                    "x": [point["timestamp"] for point in channel_points],
                    "y": [point["value"] for point in channel_points],
                }
                for channel, channel_points in grouped.items()
            ],
        }
    if mode == "bar":
        return {
            "mode": mode,
            "labels": list(grouped),
            "values": [
                next((_numeric(point) for point in reversed(channel_points) if _numeric(point) is not None), 0.0)
                for channel_points in grouped.values()
            ],
        }
    if mode == "histogram":
        series = []
        for channel, channel_points in grouped.items():
            values = [value for value in map(_numeric, channel_points) if value is not None]
            if not values:
                continue
            if np is not None:
                counts, edges = np.histogram(values, bins=max(2, bins))
                series.append({"channel": channel, "counts": counts.tolist(), "edges": edges.tolist()})
            else:
                series.append(_histogram_python(channel, values, max(2, bins)))
        return {"mode": mode, "series": series}
    if mode == "spectrum":
        series = []
        for channel, channel_points in grouped.items():
            spectrum = _spectrum(channel, channel_points)
            if spectrum:
                series.append(spectrum)
        return {"mode": mode, "series": series}
    if mode == "logic":
        series = []
        for index, (channel, channel_points) in enumerate(grouped.items()):
            transitions = []
            previous = None
            for point in channel_points:
                value = 1 if bool(point["value"]) else 0
                if value != previous:
                    transitions.append({"timestamp": point["timestamp"], "value": value})
                    previous = value
            series.append({"channel": channel, "lane": index, "transitions": transitions})
        return {"mode": mode, "series": series}
    raise ValueError(f"Unknown plot mode: {mode}")


def _histogram_python(channel: str, values: list[float], bins: int) -> dict[str, Any]:
    low, high = min(values), max(values)
    if high == low:
        return {"channel": channel, "counts": [len(values)], "edges": [low, high]}
    width = (high - low) / bins
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, int((value - low) / width))
        counts[index] += 1
    return {"channel": channel, "counts": counts, "edges": [low + index * width for index in range(bins + 1)]}


def _spectrum(channel: str, points: list[dict[str, Any]]) -> dict[str, Any] | None:
    numeric_points = [(float(point["timestamp"]), _numeric(point)) for point in points]
    numeric_points = [(timestamp, value) for timestamp, value in numeric_points if value is not None]
    if len(numeric_points) < 4:
        return None
    timestamps = [item[0] for item in numeric_points]
    values = [float(item[1]) for item in numeric_points]
    duration = timestamps[-1] - timestamps[0]
    if duration <= 0:
        return None
    sample_rate = (len(timestamps) - 1) / duration
    if np is not None:
        array = np.asarray(values, dtype=float)
        array = array - np.mean(array)
        window = np.hanning(len(array))
        spectrum = np.fft.rfft(array * window)
        frequencies = np.fft.rfftfreq(len(array), d=1.0 / sample_rate)
        magnitudes = np.abs(spectrum) * 2.0 / max(1, len(array))
        return {
            "channel": channel,
            "frequency": frequencies.tolist(),
            "magnitude": magnitudes.tolist(),
            "sample_rate": sample_rate,
        }
    # Slow but deterministic fallback for small offline datasets.
    count = len(values)
    frequencies = []
    magnitudes = []
    mean = sum(values) / count
    centered = [value - mean for value in values]
    for frequency_index in range(count // 2 + 1):
        real = 0.0
        imag = 0.0
        for sample_index, value in enumerate(centered):
            angle = -2.0 * math.pi * frequency_index * sample_index / count
            real += value * math.cos(angle)
            imag += value * math.sin(angle)
        frequencies.append(frequency_index * sample_rate / count)
        magnitudes.append(2.0 * math.sqrt(real * real + imag * imag) / count)
    return {"channel": channel, "frequency": frequencies, "magnitude": magnitudes, "sample_rate": sample_rate}
