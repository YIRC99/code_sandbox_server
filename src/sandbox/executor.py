import asyncio
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from sandbox.config import Settings

_TRUNCATION_MARKER = b"\n[output truncated]\n"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    status: str


class SandboxExecutor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._runner = Path(__file__).with_name("runner.py").resolve()

    async def execute(self, script: Path, timeout: float) -> ExecutionResult:
        if timeout <= 0 or timeout > self._settings.max_timeout_seconds:
            raise ValueError(
                f"timeout must be between 0 and {self._settings.max_timeout_seconds} seconds"
            )
        process_options: dict[str, object]
        if os.name == "nt":
            process_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        else:
            process_options = {"start_new_session": True}

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-X",
            "utf8",
            "-I",
            str(self._runner),
            str(script),
            cwd=str(script.parent),
            env=self._child_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **process_options,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task = asyncio.create_task(self._read_limited(process.stdout))
        stderr_task = asyncio.create_task(self._read_limited(process.stderr))
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except TimeoutError:
            timed_out = True
            await self._terminate_process_tree(process)

        stdout_bytes, stderr_bytes = await asyncio.gather(stdout_task, stderr_task)
        if timed_out:
            return ExecutionResult(
                stdout=self._decode(stdout_bytes),
                stderr=self._decode(stderr_bytes),
                exit_code=-1,
                status="timeout",
            )
        exit_code = process.returncode if process.returncode is not None else -2
        return ExecutionResult(
            stdout=self._decode(stdout_bytes),
            stderr=self._decode(stderr_bytes),
            exit_code=exit_code,
            status="success" if exit_code == 0 else "error",
        )

    async def _read_limited(self, reader: asyncio.StreamReader) -> bytes:
        retained = bytearray()
        truncated = False
        while chunk := await reader.read(64 * 1024):
            remaining = self._settings.max_output_bytes - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
        if truncated:
            retained.extend(_TRUNCATION_MARKER)
        return bytes(retained)

    async def _terminate_process_tree(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if os.name == "nt":
            await self._terminate_windows_tree(process)
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(
                process.wait(), timeout=self._settings.terminate_grace_seconds
            )
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    async def _terminate_windows_tree(self, process: asyncio.subprocess.Process) -> None:
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        except OSError:
            process.kill()
        try:
            await asyncio.wait_for(
                process.wait(), timeout=self._settings.terminate_grace_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()

    def _child_environment(self) -> dict[str, str]:
        environment = {
            "HOME": "/tmp/sandbox",
            "LANG": "C.UTF-8",
            "PATH": os.defpath,
            "SANDBOX_LIMIT_CPU_SECONDS": str(self._settings.process_cpu_seconds),
            "SANDBOX_LIMIT_MEMORY_BYTES": str(self._settings.process_memory_bytes),
            "SANDBOX_LIMIT_PROCESS_COUNT": str(self._settings.process_count_limit),
            "SANDBOX_LIMIT_FILE_BYTES": str(self._settings.process_file_bytes),
            "SANDBOX_LIMIT_OPEN_FILES": str(self._settings.process_open_files),
        }
        if os.name == "nt":
            for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
                if value := os.environ.get(name):
                    environment[name] = value
        return environment

    @staticmethod
    def _decode(value: bytes) -> str:
        return value.decode("utf-8", errors="replace").replace("\r\n", "\n")
