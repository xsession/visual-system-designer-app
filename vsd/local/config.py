from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

try:
    from platformdirs import user_data_path
except ImportError:  # pragma: no cover - fallback for minimal/offline environments
    user_data_path = None


@dataclass(frozen=True)
class LocalConfig:
    data_dir: Path
    workspace: Path
    host: str = "127.0.0.1"
    port: int = 9010
    api_token: str | None = None
    allow_remote: bool = False

    @classmethod
    def from_env(
        cls,
        *,
        data_dir: str | Path | None = None,
        workspace: str | Path | None = None,
        host: str | None = None,
        port: int | None = None,
        allow_remote: bool = False,
    ) -> "LocalConfig":
        if data_dir is None:
            data_dir = os.environ.get("VSD_LOCAL_DATA_DIR")
        if data_dir is None:
            if user_data_path is not None:
                data_dir = user_data_path("visual-system-designer", "CodeLayer")
            else:
                data_dir = Path.home() / ".local" / "share" / "visual-system-designer"
        data_path = Path(data_dir).expanduser().resolve()

        if workspace is None:
            workspace = os.environ.get("VSD_WORKSPACE", str(Path.cwd()))
        workspace_path = Path(workspace).expanduser().resolve()

        resolved_host = host or os.environ.get("VSD_STUDIO_HOST", "127.0.0.1")
        resolved_port = int(port or os.environ.get("VSD_STUDIO_PORT", "9010"))
        remote = allow_remote or resolved_host not in {"127.0.0.1", "localhost", "::1"}

        data_path.mkdir(parents=True, exist_ok=True)
        token = os.environ.get("VSD_STUDIO_TOKEN")
        if remote and not token:
            token = _load_or_create_token(data_path / "studio.token")

        return cls(
            data_dir=data_path,
            workspace=workspace_path,
            host=resolved_host,
            port=resolved_port,
            api_token=token,
            allow_remote=remote,
        )


def _load_or_create_token(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return token
