import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from sandbox.config import Settings
from sandbox.main import create_app


def make_client(tmp_path: Path, **overrides: object) -> TestClient:
    values: dict[str, object] = {
        "upload_dir": tmp_path,
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
        uploaded = upload(client, "random_123.py", "print('from api')")
        identity = uploaded.json()
        executed = client.post("/execute", headers=auth_headers(), json=identity)

    assert uploaded.status_code == 201
    assert set(identity) == {"date", "filename"}
    assert identity["filename"] == "random_123.py"
    assert executed.status_code == 200
    assert executed.json() == {
        "stdout": "from api\n",
        "stderr": "",
        "exit_code": 0,
        "status": "success",
    }


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
            json={"date": "../2026-07-17", "filename": "x.py"},
        )
        missing = client.post(
            "/execute",
            headers=auth_headers(),
            json={"date": "2026-07-17", "filename": "missing.py"},
        )
        uploaded = upload(client, "slow.py", "pass").json()
        excessive = client.post("/execute", headers=auth_headers(), json={**uploaded, "timeout": 3})

    assert invalid.status_code == 400
    assert missing.status_code == 404
    assert excessive.status_code == 400


def test_execute_reports_nonzero_exit_as_error(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        identity = upload(client, "error.py", "raise RuntimeError('bad')").json()
        response = client.post("/execute", headers=auth_headers(), json=identity)

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["exit_code"] != 0
    assert "RuntimeError: bad" in response.json()["stderr"]


def test_execute_returns_429_when_execution_queue_is_full(tmp_path: Path) -> None:
    with make_client(
        tmp_path,
        max_concurrent=1,
        max_waiting=0,
        queue_wait_seconds=0.1,
    ) as client:
        identity = upload(client, "slow.py", "import time\ntime.sleep(0.5)").json()

        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(
                client.post,
                "/execute",
                headers=auth_headers(),
                json={**identity, "timeout": 1},
            )
            for _ in range(100):
                if client.app.state.gate.active == 1:
                    break
                time.sleep(0.005)
            second = client.post(
                "/execute",
                headers=auth_headers(),
                json={**identity, "timeout": 1},
            )
            assert first.result().status_code == 200

    assert second.status_code == 429
    assert second.json() == {"detail": "sandbox is busy; retry later"}


def test_unexpected_execute_error_does_not_leak_traceback(tmp_path: Path) -> None:
    class BrokenExecutor:
        async def execute(self, script: Path, timeout: float):
            raise RuntimeError(f"secret path: {script}")

    app = create_app(Settings(upload_dir=tmp_path, api_key="test-key", cleanup_interval_seconds=60))
    app.state.executor = BrokenExecutor()
    with TestClient(app) as client:
        identity = upload(client, "broken.py", "pass").json()
        response = client.post("/execute", headers=auth_headers(), json=identity)

    assert response.status_code == 500
    assert response.json() == {"detail": "sandbox execution failed"}
    assert str(tmp_path) not in response.text
    assert "Traceback" not in response.text
