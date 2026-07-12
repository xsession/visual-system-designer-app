# Local Studio architecture

## Design goals

The Local Studio is an additive subsystem. It does not replace the existing Pipeline Manager editor or Zephyr/Renode integration. It supplies a fully local path for project design, assets, emulation sessions, reverse engineering and telemetry.

## Storage

`LocalStore` uses one SQLite database in WAL mode for metadata and time-series records. Binary assets are stored by SHA-256 under:

```text
<data-dir>/assets/sha256/aa/bb/<complete-sha256>
```

The database records the original name, MIME type, size and hash. Reimporting identical bytes returns the existing asset rather than duplicating them.

Default data directories follow the operating system through `platformdirs`. `VSD_LOCAL_DATA_DIR` overrides the location.

## Services

- `LocalStore`: components, projects, assets, sessions, telemetry, logs and analysis reports.
- `FirmwareAnalyzer`: local static firmware analysis and comparison.
- `TelemetryHub`: bounded live subscriptions with persistent ingestion.
- `EmulationManager`: shell-free subprocess orchestration for Renode, OpenOCD and user-selected executables.
- FastAPI: local API and WebSocket transport.
- Static Studio UI: no CDN, remote font, analytics or hosted database dependencies.

## Component catalog

The bundled CSV contains 500 external components. IDs are namespaced by kind internally, so controllers appearing in more than one category do not overwrite each other. Visual nodes retain the Renode class, bus type, vendor, fidelity tier and source slug.

## Compatibility

The overlay patches only:

- `pyproject.toml`, to add extras, package data and a standalone command;
- `vsd/__main__.py`, to register `vsd studio`.

All other files are additive.
