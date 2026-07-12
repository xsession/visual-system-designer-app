# Observability and live filtering

## Ingestion

Telemetry can arrive through:

- `POST /api/sessions/{id}/telemetry`;
- process output lines in either format:
  - `VSD:name=value`
  - `VSD_TELEMETRY {"channel":"name","value":1.23,"kind":"analog"}`
- the local WebSocket stream for UI updates.

Each point stores timestamp, channel, value, kind and JSON metadata.

## Plot modes

- **Time plot**: ordinary time-series view.
- **Scope**: zero-centered multi-channel trace view.
- **Bar plot**: latest value per channel.
- **Histogram**: configurable binning per channel.
- **Spectrum**: windowed real FFT with a pure-Python fallback.
- **Logic analyzer**: transition-only digital lanes.

## Safe filters

Expressions are parsed with Python AST but only literals, boolean operations, comparisons and arithmetic over `value`, `channel`, `kind` and `timestamp` are accepted. Function calls, attribute access, imports and comprehensions are rejected.

Examples:

```text
channel == "temperature" and value > 30
kind == "digital" and value == 1
timestamp >= 1720000000
```

## Pipeline operators

The UI accepts an ordered JSON array:

```json
[
  {"op": "moving_average", "window": 16},
  {"op": "lowpass", "alpha": 0.2},
  {"op": "downsample", "factor": 4}
]
```

Available operators are moving average, EMA, low-pass, high-pass, derivative, integral, downsample, clamp and normalize.
