import asyncio
import shutil
import threading
from pathlib import Path

import pytest

from sandbox.config import Settings
from sandbox.executor import SandboxExecutor


def write_script(tmp_path: Path, source: str, name: str = "script.py") -> Path:
    script = tmp_path / name
    script.write_text(source, encoding="utf-8")
    return script


def write_data_file(tmp_path: Path, content: str = "date,close\n2026-07-21,101.5\n") -> Path:
    data_file = tmp_path / "market.csv"
    data_file.write_text(content, encoding="utf-8")
    return data_file


@pytest.mark.asyncio
async def test_execute_returns_stdout_for_success(tmp_path: Path) -> None:
    executor = SandboxExecutor(Settings(upload_dir=tmp_path))
    script = write_script(
        tmp_path,
        "import sys\nfrom pathlib import Path\nprint(Path(sys.argv[1]).read_text(), end='')",
    )
    data_file = write_data_file(tmp_path)

    result = await executor.execute(script, data_file, timeout=1)

    assert result.status == "success"
    assert result.exit_code == 0
    assert result.stdout == "date,close\n2026-07-21,101.5\n"
    assert result.stderr == ""


@pytest.mark.asyncio
async def test_execute_maps_nonzero_exit_to_error(tmp_path: Path) -> None:
    executor = SandboxExecutor(Settings(upload_dir=tmp_path))
    script = write_script(tmp_path, "import sys\nprint('bad', file=sys.stderr)\nsys.exit(7)")
    data_file = write_data_file(tmp_path)

    result = await executor.execute(script, data_file, timeout=1)

    assert result.status == "error"
    assert result.exit_code == 7
    assert result.stderr == "bad\n"


@pytest.mark.asyncio
async def test_execute_hides_uploaded_file_absolute_path(tmp_path: Path) -> None:
    executor = SandboxExecutor(Settings(upload_dir=tmp_path))
    script = write_script(tmp_path, "raise RuntimeError('bad')")
    data_file = write_data_file(tmp_path)

    result = await executor.execute(script, data_file, timeout=1)

    assert str(tmp_path) not in result.stderr
    assert 'File "script.py"' in result.stderr


@pytest.mark.asyncio
async def test_execute_times_out(tmp_path: Path) -> None:
    executor = SandboxExecutor(Settings(upload_dir=tmp_path, terminate_grace_seconds=0.1))
    script = write_script(tmp_path, "import time\nprint('started', flush=True)\ntime.sleep(5)")
    data_file = write_data_file(tmp_path)

    result = await executor.execute(script, data_file, timeout=0.1)

    assert result.status == "timeout"
    assert result.exit_code == -1
    assert result.stdout == "started\n"


@pytest.mark.asyncio
async def test_execute_truncates_and_drains_large_output(tmp_path: Path) -> None:
    executor = SandboxExecutor(Settings(upload_dir=tmp_path, max_output_bytes=8))
    script = write_script(tmp_path, "print('x' * 100_000)")
    data_file = write_data_file(tmp_path)

    result = await executor.execute(script, data_file, timeout=2)

    assert result.status == "success"
    assert result.stdout.startswith("xxxxxxxx")
    assert result.stdout.endswith("\n[output truncated]\n")


@pytest.mark.asyncio
async def test_execute_replaces_invalid_utf8(tmp_path: Path) -> None:
    executor = SandboxExecutor(Settings(upload_dir=tmp_path))
    script = write_script(tmp_path, "import sys\nsys.stdout.buffer.write(b'\\xff')")
    data_file = write_data_file(tmp_path)

    result = await executor.execute(script, data_file, timeout=1)

    assert result.stdout == "�"


@pytest.mark.asyncio
async def test_execute_does_not_expose_parent_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SHOULD_NOT_LEAK", "top-secret")
    executor = SandboxExecutor(Settings(upload_dir=tmp_path))
    script = write_script(
        tmp_path,
        "import os\nprint(os.environ.get('SHOULD_NOT_LEAK', 'missing'))",
    )
    data_file = write_data_file(tmp_path)

    result = await executor.execute(script, data_file, timeout=1)

    assert result.stdout == "missing\n"


@pytest.mark.asyncio
async def test_execute_uses_disposable_working_directory(tmp_path: Path) -> None:
    executor = SandboxExecutor(Settings(upload_dir=tmp_path))
    script = write_script(
        tmp_path,
        "from pathlib import Path\nPath('artifact.txt').write_text('temporary')",
    )
    data_file = write_data_file(tmp_path)

    result = await executor.execute(script, data_file, timeout=1)

    assert result.status == "success"
    assert not (tmp_path / "artifact.txt").exists()


@pytest.mark.asyncio
async def test_execute_keeps_event_loop_responsive_while_copying_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    copy_started = threading.Event()
    release_copy = threading.Event()
    original_copyfile = shutil.copyfile

    def blocking_copyfile(source: Path, destination: Path) -> str:
        copy_started.set()
        release_copy.wait(timeout=1)
        return original_copyfile(source, destination)

    monkeypatch.setattr(shutil, "copyfile", blocking_copyfile)
    executor = SandboxExecutor(Settings(upload_dir=tmp_path))
    script = write_script(tmp_path, "print('copied')")
    data_file = write_data_file(tmp_path)
    safety_release = threading.Timer(0.2, release_copy.set)
    safety_release.start()

    execution = asyncio.create_task(executor.execute(script, data_file, timeout=1))
    await asyncio.sleep(0.05)
    copy_started_before_deadline = copy_started.is_set()
    event_loop_was_responsive = not release_copy.is_set()
    release_copy.set()
    result = await execution
    safety_release.cancel()

    assert copy_started_before_deadline
    assert event_loop_was_responsive
    assert result.status == "success"


@pytest.mark.asyncio
async def test_execute_allows_future_imports(tmp_path: Path) -> None:
    executor = SandboxExecutor(Settings(upload_dir=tmp_path))
    script = write_script(tmp_path, "from __future__ import annotations\nprint('ok')")
    data_file = write_data_file(tmp_path)

    result = await executor.execute(script, data_file, timeout=1)

    assert result.status == "success"
    assert result.stdout == "ok\n"


@pytest.mark.asyncio
async def test_execute_blocks_unauthorized_imports(tmp_path: Path) -> None:
    executor = SandboxExecutor(Settings(upload_dir=tmp_path))
    script = write_script(tmp_path, "import socket\nprint('connected')")
    data_file = write_data_file(tmp_path)

    result = await executor.execute(script, data_file, timeout=1)

    assert result.status == "error"
    assert result.exit_code != 0
    assert "Importing module 'socket' is not allowed by sandbox policy" in result.stderr


@pytest.mark.asyncio
async def test_execute_blocks_dynamic_dangerous_audit_events(tmp_path: Path) -> None:
    executor = SandboxExecutor(Settings(upload_dir=tmp_path))
    script = write_script(tmp_path, "eval(\"__import__('os').system('echo pwned')\")")
    data_file = write_data_file(tmp_path)

    result = await executor.execute(script, data_file, timeout=1)

    assert result.status == "error"
    assert result.exit_code != 0
    assert "Operation 'os.system' is blocked by sandbox runtime audit policy" in result.stderr


@pytest.mark.asyncio
async def test_timeout_kills_long_running_script(tmp_path: Path) -> None:
    script = write_script(
        tmp_path,
        "import time\nprint('started', flush=True)\ntime.sleep(5)\n",
    )
    executor = SandboxExecutor(
        Settings(
            upload_dir=tmp_path,
            terminate_grace_seconds=0.1,
        )
    )
    data_file = write_data_file(tmp_path)

    result = await executor.execute(script, data_file, timeout=0.1)

    assert result.status == "timeout"
    assert result.exit_code == -1
    assert result.stdout == "started\n"


@pytest.mark.asyncio
async def test_execute_fallback_when_asyncio_subprocess_not_implemented(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def mock_create_subprocess_exec(*args, **kwargs):
        raise NotImplementedError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess_exec)

    executor = SandboxExecutor(Settings(upload_dir=tmp_path))
    script = write_script(
        tmp_path,
        "import sys\nfrom pathlib import Path\nprint(Path(sys.argv[1]).read_text(), end='')",
    )
    data_file = write_data_file(tmp_path, "fallback,data\nworks,1\n")

    result = await executor.execute(script, data_file, timeout=1)

    assert result.status == "success"
    assert result.exit_code == 0
    assert result.stdout == "fallback,data\nworks,1\n"

