import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from sandbox.config import Settings
from sandbox.main import create_app


def make_client(tmp_path: Path, **overrides: object) -> TestClient:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "input.csv").write_text("date,close\n2026-07-21,101.5\n", encoding="utf-8")
    values: dict[str, object] = {
        "upload_dir": tmp_path,
        "data_dir": data_dir,
        "api_key": "test-key",
        "max_upload_bytes": 1024,
        "max_timeout_seconds": 2,
        "cleanup_interval_seconds": 60,
    }
    values.update(overrides)
    return TestClient(create_app(Settings(**values)))


def auth_headers() -> dict[str, str]:
    return {"X-API-Key": "test-key"}


def upload(client: TestClient, filename: str, source: str):
    return client.post(
        "/upload",
        headers=auth_headers(),
        files={"file": (filename, source.encode("utf-8"), "text/x-python")},
    )


def execution_body(identity: dict[str, str], **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {**identity, "data_file": "input.csv"}
    body.update(overrides)
    return body


def test_health_does_not_require_authentication(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_business_endpoints_require_valid_api_key(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        missing = client.post("/upload", files={"file": ("a.py", b"pass")})
        wrong = client.post(
            "/upload",
            headers={"X-API-Key": "wrong"},
            files={"file": ("a.py", b"pass")},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.json() == {"detail": "invalid API key"}


def test_upload_and_execute_preserve_date_filename_contract(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        uploaded = upload(
            client,
            "random_123.py",
            "import sys\nfrom pathlib import Path\nprint(Path(sys.argv[1]).read_text(), end='')",
        )
        identity = uploaded.json()
        body = execution_body(identity)
        executed = client.post("/execute", headers=auth_headers(), json=body)
        repeated = client.post("/execute", headers=auth_headers(), json=body)

    assert uploaded.status_code == 201
    assert set(identity) == {"date", "filename"}
    assert identity["filename"] == "random_123.py"
    assert executed.status_code == 200
    assert executed.json() == {
        "stdout": "date,close\n2026-07-21,101.5\n",
        "stderr": "",
        "exit_code": 0,
        "status": "success",
    }
    assert repeated.status_code == 200
    assert repeated.json() == executed.json()
    assert (tmp_path / identity["date"] / identity["filename"]).exists()


def test_upload_rejects_invalid_duplicate_and_large_files(tmp_path: Path) -> None:
    with make_client(tmp_path, max_upload_bytes=4) as client:
        invalid = upload(client, "../unsafe.py", "pass")
        first = upload(client, "same.py", "1234")
        duplicate = upload(client, "same.py", "pass")
        large = upload(client, "large.py", "12345")

    assert invalid.status_code == 400
    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert large.status_code == 413


def test_execute_rejects_invalid_missing_and_excessive_timeout(tmp_path: Path) -> None:
    with make_client(tmp_path, max_timeout_seconds=2) as client:
        invalid = client.post(
            "/execute",
            headers=auth_headers(),
            json={"date": "../2026-07-17", "filename": "x.py", "data_file": "input.csv"},
        )
        missing = client.post(
            "/execute",
            headers=auth_headers(),
            json={
                "date": "2026-07-17",
                "filename": "missing.py",
                "data_file": "input.csv",
            },
        )
        uploaded = upload(client, "slow.py", "pass").json()
        excessive = client.post(
            "/execute", headers=auth_headers(), json=execution_body(uploaded, timeout=3)
        )

    assert invalid.status_code == 400
    assert missing.status_code == 404
    assert excessive.status_code == 400


def test_execute_rejects_unsafe_or_missing_data_paths_without_leaking_root(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        identity = upload(client, "data_paths.py", "pass").json()
        absolute = client.post(
            "/execute",
            headers=auth_headers(),
            json=execution_body(
                identity,
                data_file=str((tmp_path / "data" / "input.csv").resolve()),
            ),
        )
        traversal = client.post(
            "/execute",
            headers=auth_headers(),
            json=execution_body(identity, data_file="../input.csv"),
        )
        non_csv = client.post(
            "/execute",
            headers=auth_headers(),
            json=execution_body(identity, data_file="input.txt"),
        )
        missing = client.post(
            "/execute",
            headers=auth_headers(),
            json=execution_body(identity, data_file="missing.csv"),
        )

    assert absolute.status_code == 400
    assert traversal.status_code == 400
    assert non_csv.status_code == 400
    assert missing.status_code == 404
    for response in (absolute, traversal, non_csv, missing):
        assert str(tmp_path) not in response.text


def test_execute_rejects_windows_unsafe_data_path_segments(tmp_path: Path) -> None:
    unsafe_paths = (
        "market/data:secret.csv",
        "NUL.csv",
        "nested/COM1.csv",
        "nested/AUX.report.csv",
        "nested/NUL .csv",
        "nested/COM1..csv",
        "nested/conin$.csv",
        "CONOUT$.report.csv",
        "nested/COM¹.csv",
        "market/com².report.csv",
        "nested/COM³.csv",
        "market/LPT¹.csv",
        "nested/lpt².report.csv",
        "nested/LPT³ .csv",
        "market/bad?.csv",
        "market/control\x1f.csv",
        "market/delete\x7f.csv",
        "market/nul\x00.csv",
    )
    with make_client(tmp_path) as client:
        identity = upload(client, "unsafe_windows_paths.py", "pass").json()
        responses = [
            client.post(
                "/execute",
                headers=auth_headers(),
                json=execution_body(identity, data_file=data_file),
            )
            for data_file in unsafe_paths
        ]

    for data_file, response in zip(unsafe_paths, responses, strict=True):
        assert response.status_code == 400, data_file
        assert response.json() == {"detail": "data_file contains an unsafe path segment"}
        assert str(tmp_path) not in response.text


def test_execute_accepts_safe_unicode_data_subdirectory(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        data_file = tmp_path / "data" / "行情" / "日线.csv"
        data_file.parent.mkdir()
        data_file.write_text("日期,收盘\n2026-07-21,101.5\n", encoding="utf-8")
        identity = upload(
            client,
            "unicode_data.py",
            "import sys\nfrom pathlib import Path\nprint(Path(sys.argv[1]).read_text(), end='')",
        ).json()
        response = client.post(
            "/execute",
            headers=auth_headers(),
            json=execution_body(identity, data_file="行情/日线.csv"),
        )

    assert response.status_code == 200
    assert response.json()["stdout"] == "日期,收盘\n2026-07-21,101.5\n"


def test_execute_rejects_oversized_data_file_without_leaking_root(tmp_path: Path) -> None:
    with make_client(tmp_path, max_data_file_bytes=8) as client:
        identity = upload(client, "oversized_data.py", "pass").json()
        response = client.post(
            "/execute",
            headers=auth_headers(),
            json=execution_body(identity),
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "data file exceeds configured size limit"}
    assert str(tmp_path) not in response.text


def test_execute_reports_nonzero_exit_as_error(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        identity = upload(client, "error.py", "raise RuntimeError('bad')").json()
        response = client.post("/execute", headers=auth_headers(), json=execution_body(identity))

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["exit_code"] != 0
    assert "RuntimeError: bad" in response.json()["stderr"]


def test_upload_can_be_executed_twice_concurrently(tmp_path: Path) -> None:
    with make_client(tmp_path, max_concurrent=2) as client:
        identity = upload(client, "repeatable.py", "import time\ntime.sleep(0.3)").json()

        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(
                client.post,
                "/execute",
                headers=auth_headers(),
                json=execution_body(identity),
            )
            for _ in range(100):
                if client.app.state.gate.active == 1:
                    break
                time.sleep(0.005)
            repeated = client.post(
                "/execute", headers=auth_headers(), json=execution_body(identity)
            )
            assert first.result().status_code == 200

    assert repeated.status_code == 200
    assert (tmp_path / identity["date"] / identity["filename"]).exists()


def test_execute_returns_429_when_execution_queue_is_full(tmp_path: Path) -> None:
    with make_client(
        tmp_path,
        max_concurrent=1,
        max_waiting=0,
        queue_wait_seconds=0.1,
    ) as client:
        slow_identity = upload(client, "slow.py", "import time\ntime.sleep(0.5)").json()
        queued_identity = upload(client, "queued.py", "pass").json()

        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(
                client.post,
                "/execute",
                headers=auth_headers(),
                json=execution_body(slow_identity, timeout=1),
            )
            for _ in range(100):
                if client.app.state.gate.active == 1:
                    break
                time.sleep(0.005)
            second = client.post(
                "/execute",
                headers=auth_headers(),
                json=execution_body(queued_identity, timeout=1),
            )
            assert first.result().status_code == 200

    assert second.status_code == 429
    assert second.json() == {"detail": "sandbox is busy; retry later"}


def test_unexpected_execute_error_does_not_leak_traceback(tmp_path: Path) -> None:
    class BrokenExecutor:
        async def execute(self, script: Path, data_file: Path, timeout: float):
            raise RuntimeError(f"secret path: {script}")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "input.csv").write_text("value\n1\n", encoding="utf-8")
    app = create_app(
        Settings(
            upload_dir=tmp_path,
            data_dir=data_dir,
            api_key="test-key",
            cleanup_interval_seconds=60,
        )
    )
    app.state.executor = BrokenExecutor()
    with TestClient(app) as client:
        identity = upload(client, "broken.py", "pass").json()
        response = client.post("/execute", headers=auth_headers(), json=execution_body(identity))

    assert response.status_code == 500
    assert response.json() == {"detail": "sandbox execution failed"}
    assert str(tmp_path) not in response.text
    assert "Traceback" not in response.text
    assert (tmp_path / identity["date"] / identity["filename"]).exists()
