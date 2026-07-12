from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from .models import TelemetryPoint
from .store import LocalStore
from .telemetry import TelemetryHub, parse_telemetry_line


@dataclass(slots=True)
class ToolchainStatus:
    renode: str | None
    openocd: str | None
    gdb: str | None
    objdump: str | None
    readelf: str | None

    @classmethod
    def discover(cls) -> "ToolchainStatus":
        return cls(
            renode=_find_tool("RENODE_BIN", ("renode", "Renode")),
            openocd=_find_tool("OPENOCD_BIN", ("openocd",)),
            gdb=_find_tool("GDB_BIN", ("gdb-multiarch", "arm-none-eabi-gdb", "gdb")),
            objdump=_find_tool("OBJDUMP_BIN", ("llvm-objdump", "arm-none-eabi-objdump", "objdump")),
            readelf=_find_tool("READELF_BIN", ("llvm-readelf", "readelf")),
        )

    def as_dict(self) -> dict[str, str | None]:
        return {
            "renode": self.renode,
            "openocd": self.openocd,
            "gdb": self.gdb,
            "objdump": self.objdump,
            "readelf": self.readelf,
        }


def _find_tool(env_name: str, candidates: Sequence[str]) -> str | None:
    configured = os.environ.get(env_name)
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return str(path.resolve())
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


@dataclass
class RunningSession:
    id: str
    process: asyncio.subprocess.Process
    command: list[str]
    tasks: list[asyncio.Task] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)


class EmulationManager:
    """Manage local Renode/OpenOCD/custom bare-metal processes.

    Commands are always passed as argument arrays. Shell execution is never used.
    A process group is created so a stop operation also terminates child processes.
    """

    def __init__(self, store: LocalStore, telemetry: TelemetryHub):
        self.store = store
        self.telemetry = telemetry
        self.toolchain = ToolchainStatus.discover()
        self._sessions: dict[str, RunningSession] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        target: str,
        firmware: Path | None = None,
        platform: Path | None = None,
        script: Path | None = None,
        project_id: str | None = None,
        firmware_asset_id: int | None = None,
        extra_args: Sequence[str] = (),
        custom_command: Sequence[str] | None = None,
    ) -> str:
        command = self._build_command(
            target=target,
            firmware=firmware,
            platform=platform,
            script=script,
            extra_args=extra_args,
            custom_command=custom_command,
        )
        session_id = self.store.create_session(
            target=target,
            project_id=project_id,
            firmware_asset_id=firmware_asset_id,
            command=command,
            metadata={"platform": str(platform) if platform else None, "script": str(script) if script else None},
        )
        self.store.append_log(
            session_id=session_id,
            source="emulation",
            message="Starting: " + " ".join(command),
        )
        creation_flags = 0
        start_new_session = os.name != "nt"
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.store.root),
                start_new_session=start_new_session,
                creationflags=creation_flags,
            )
        except Exception as error:
            self.store.update_session(session_id, status="failed", stopped=True)
            self.store.append_log(
                session_id=session_id,
                source="emulation",
                level="ERROR",
                message=f"Failed to start process: {error}",
            )
            raise

        running = RunningSession(id=session_id, process=process, command=command)
        running.tasks = [
            asyncio.create_task(self._pump_stream(running, process.stdout, "stdout", "INFO")),
            asyncio.create_task(self._pump_stream(running, process.stderr, "stderr", "ERROR")),
            asyncio.create_task(self._wait_for_exit(running)),
        ]
        async with self._lock:
            self._sessions[session_id] = running
        self.store.update_session(session_id, status="running")
        return session_id

    def _build_command(
        self,
        *,
        target: str,
        firmware: Path | None,
        platform: Path | None,
        script: Path | None,
        extra_args: Sequence[str],
        custom_command: Sequence[str] | None,
    ) -> list[str]:
        normalized = target.lower()
        if custom_command:
            if not custom_command:
                raise ValueError("Custom command cannot be empty")
            executable = shutil.which(custom_command[0]) or custom_command[0]
            return [str(executable), *map(str, custom_command[1:])]
        if normalized == "renode":
            if not self.toolchain.renode:
                raise FileNotFoundError("Renode was not found. Set RENODE_BIN or add it to PATH.")
            command = [self.toolchain.renode, "--disable-xwt", "--console"]
            if script:
                command.extend(["--execute", f"include @{script.resolve()}"])
            elif platform:
                execute = ["mach create", f"machine LoadPlatformDescription @{platform.resolve()}"]
                if firmware:
                    if firmware.suffix.lower() == ".elf":
                        execute.append(f"sysbus LoadELF @{firmware.resolve()}")
                    elif firmware.suffix.lower() in {".hex", ".ihex", ".ihx"}:
                        execute.append(f"sysbus LoadHEX @{firmware.resolve()}")
                    else:
                        execute.append(f"sysbus LoadBinary @{firmware.resolve()} 0x0")
                execute.append("start")
                command.extend(["--execute", "; ".join(execute)])
            else:
                raise ValueError("Renode sessions require a .resc script or a .repl platform")
            command.extend(map(str, extra_args))
            return command
        if normalized == "openocd":
            if not self.toolchain.openocd:
                raise FileNotFoundError("OpenOCD was not found. Set OPENOCD_BIN or add it to PATH.")
            if not script:
                raise ValueError("OpenOCD sessions require a configuration script")
            return [self.toolchain.openocd, "-f", str(script.resolve()), *map(str, extra_args)]
        if normalized in {"native", "custom"}:
            if not firmware:
                raise ValueError("Native sessions require an executable firmware/tool path")
            return [str(firmware.resolve()), *map(str, extra_args)]
        raise ValueError(f"Unsupported emulation target: {target}")

    async def stop(self, session_id: str, timeout: float = 5.0) -> bool:
        async with self._lock:
            running = self._sessions.get(session_id)
        if not running:
            session = self.store.get_session(session_id)
            return bool(session and session["status"] in {"stopped", "exited", "failed"})
        process = running.process
        if process.returncode is None:
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except (ProcessLookupError, asyncio.TimeoutError):
                if process.returncode is None:
                    if os.name == "nt":
                        process.kill()
                    else:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    await process.wait()
        self.store.update_session(session_id, status="stopped", stopped=True)
        self.store.append_log(session_id=session_id, source="emulation", message="Session stopped")
        return True

    async def stop_all(self) -> None:
        async with self._lock:
            ids = list(self._sessions)
        for session_id in ids:
            await self.stop(session_id)

    async def _pump_stream(
        self,
        running: RunningSession,
        stream: asyncio.StreamReader | None,
        source: str,
        default_level: str,
    ) -> None:
        if stream is None:
            return
        while True:
            line_bytes = await stream.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
            self.store.append_log(
                session_id=running.id,
                source=source,
                level=default_level,
                message=line,
            )
            point = parse_telemetry_line(line)
            if point:
                await self.telemetry.publish(running.id, [point])

    async def _wait_for_exit(self, running: RunningSession) -> None:
        return_code = await running.process.wait()
        status = "exited" if return_code == 0 else "failed"
        self.store.update_session(running.id, status=status, stopped=True)
        self.store.append_log(
            session_id=running.id,
            source="emulation",
            level="INFO" if return_code == 0 else "ERROR",
            message=f"Process exited with code {return_code}",
        )
        async with self._lock:
            self._sessions.pop(running.id, None)
