# Security model

- Loopback binding is the default.
- Remote binding is rejected unless `--allow-remote` is set.
- Remote mode generates a random bearer token stored with owner-only permissions where supported.
- Uploaded assets are never executed automatically.
- Emulation commands use argument arrays and never invoke a shell.
- Safe telemetry expressions reject calls, imports and attribute traversal.
- Asset filenames do not determine storage paths; SHA-256 does.
- The Compose profile drops Linux capabilities, enables `no-new-privileges`, mounts toolchains read-only and uses a non-root user.

The application can explicitly launch custom local commands for reverse engineering. Anyone with write access to the API and a valid token can therefore execute tools as the Studio user. Do not expose the service directly to an untrusted network.
