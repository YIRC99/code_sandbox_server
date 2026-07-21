import asyncio
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import Settings

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
        with tempfile.TemporaryDirectory(prefix="code-sandbox-") as workspace:
            isolated_script = Path(workspace) / script.name
            shutil.copyfile(script, isolated_script)
            return await self._execute_isolated(isolated_script, timeout)

    async def _execute_isolated(self, script: Path, timeout: float) -> ExecutionResult:
        process_options: dict[str, object]
        if os.name == "nt":
            process_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        else:
            process_options = {"start_new_session": True}

        try:
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
        except NotImplementedError:
            return await asyncio.to_thread(
                self._execute_isolated_sync, script, timeout, process_options
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
        stdout = self._decode(stdout_bytes)
        stderr = self._sanitize_stderr(self._decode(stderr_bytes), script)
        if timed_out:
            return ExecutionResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=-1,
                status="timeout",
            )
        exit_code = process.returncode if process.returncode is not None else -2
        return ExecutionResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            status="success" if exit_code == 0 else "error",
        )

    def _execute_isolated_sync(
        self, script: Path, timeout: float, process_options: dict[str, object]
    ) -> ExecutionResult:
        cmd = [
            sys.executable,
            "-X",
            "utf8",
            "-I",
            str(self._runner),
            str(script),
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(script.parent),
            env=self._child_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **process_options,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None

        stdout_bytes = bytearray()
        stderr_bytes = bytearray()
        stdout_truncated = False
        stderr_truncated = False

        def read_stream(stream, container, is_stdout):
            nonlocal stdout_truncated, stderr_truncated
            while chunk := stream.read(64 * 1024):
                remaining = self._settings.max_output_bytes - len(container)
                if remaining > 0:
                    container.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    if is_stdout:
                        stdout_truncated = True
                    else:
                        stderr_truncated = True
            stream.close()

        t_out = threading.Thread(
            target=read_stream,
            args=(proc.stdout, stdout_bytes, True),
        )
        t_err = threading.Thread(
            target=read_stream,
            args=(proc.stderr, stderr_bytes, False),
        )
        t_out.start()
        t_err.start()

        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_process_tree_sync(proc)

        t_out.join()
        t_err.join()

        if stdout_truncated:
            stdout_bytes.extend(_TRUNCATION_MARKER)
        if stderr_truncated:
            stderr_bytes.extend(_TRUNCATION_MARKER)

        stdout = self._decode(bytes(stdout_bytes))
        stderr = self._sanitize_stderr(self._decode(bytes(stderr_bytes)), script)

        if timed_out:
            return ExecutionResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=-1,
                status="timeout",
            )

        exit_code = proc.returncode if proc.returncode is not None else -2
        return ExecutionResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            status="success" if exit_code == 0 else "error",
        )

    def _terminate_process_tree_sync(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except OSError:
                process.kill()
            try:
                process.wait(timeout=self._settings.terminate_grace_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=self._settings.terminate_grace_seconds)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()

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
            await asyncio.wait_for(process.wait(), timeout=self._settings.terminate_grace_seconds)
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
        except (OSError, NotImplementedError):
            process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=self._settings.terminate_grace_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()

    def _child_environment(self) -> dict[str, str]:
        environment = {
            "HOME": "/tmp",
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

    def _sanitize_stderr(self, value: str, script: Path) -> str:
        replacements = (
            (str(script), script.name),
            (script.as_posix(), script.name),
            (str(self._runner), self._runner.name),
            (self._runner.as_posix(), self._runner.name),
        )
        for absolute_path, safe_name in replacements:
            value = value.replace(absolute_path, safe_name)
        return value
