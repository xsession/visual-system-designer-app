# Deployment

## Native development

```bash
pip install -r packaging/requirements-studio.txt pytest httpx pytest-asyncio
pip install --no-deps -e .
pytest -q tests/local
vsd studio run
```

## Docker

```bash
docker compose build
docker compose up -d
```

Persistent paths:

- `./local-data` -> `/data`
- `./workspace` -> `/workspace`
- `./toolchains` -> `/toolchains` (read-only)

The default host binding is `127.0.0.1:9010`. Renode is mounted as a local toolchain instead of downloaded at container startup. This keeps runtime deployment deterministic and permits a custom Renode build.

## Desktop executable

```bash
pip install -r packaging/requirements-studio.txt pyinstaller
pip install --no-deps -e .
python packaging/build_desktop.py --clean
```

PyInstaller must run on each target operating system. The supplied GitHub Actions matrix builds:

- Linux x86-64
- Windows x86-64
- macOS x86-64
- macOS arm64

The executable contains the static UI and component catalog. External emulators and debuggers are discovered from the host PATH or variables such as `RENODE_BIN` and `OPENOCD_BIN`.

## Multi-architecture container

The release workflow builds an OCI archive for Linux `amd64` and `arm64` using Buildx/QEMU. It does not publish automatically; registry credentials and an explicit push policy can be added by the repository owner.
