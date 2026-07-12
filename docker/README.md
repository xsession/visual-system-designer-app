# Container deployment

The container keeps runtime state in `/data`, firmware workspaces in `/workspace`, and optional toolchains in `/toolchains`.
The default Compose binding is localhost-only. The Studio generates a bearer token because the process listens on `0.0.0.0` inside the container; obtain it from `docker compose logs` or `local-data/studio.token`.

```bash
docker compose build
docker compose up -d
```

Renode is intentionally mounted rather than downloaded at runtime. Put a portable Renode installation under `toolchains/renode`, or override `RENODE_BIN`. OpenOCD and `gdb-multiarch` are installed in the image.
