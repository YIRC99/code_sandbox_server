import os
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile

from sandbox.config import Settings
from sandbox.storage import (
    DuplicateUploadError,
    InvalidPathError,
    UploadStorage,
    UploadTooLargeError,
    validate_date,
    validate_filename,
)


@pytest.mark.parametrize("value", ["../2026-07-17", "2026-7-1", "not-a-date", "2026-02-30"])
def test_validate_date_rejects_invalid_values(value: str) -> None:
    with pytest.raises(InvalidPathError):
        validate_date(value)


def test_validate_date_accepts_exact_iso_date() -> None:
    assert validate_date("2026-07-17") == "2026-07-17"


@pytest.mark.parametrize(
    "value",
    ["../code.py", "folder/code.py", "folder\\code.py", ".hidden.py", "code.txt", "a b.py"],
)
def test_validate_filename_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(InvalidPathError):
        validate_filename(value)


def test_validate_filename_preserves_safe_random_name() -> None:
    assert validate_filename("aB12_34-56.py") == "aB12_34-56.py"


@pytest.mark.asyncio
async def test_save_preserves_filename_and_date(tmp_path: Path) -> None:
    settings = Settings(upload_dir=tmp_path, max_upload_bytes=32)
    storage = UploadStorage(settings)
    upload = UploadFile(filename="random123.py", file=BytesIO(b"print('ok')\n"))

    saved = await storage.save(upload)

    assert saved.date == date.today().isoformat()
    assert saved.filename == "random123.py"
    assert storage.resolve(saved.date, saved.filename).read_bytes() == b"print('ok')\n"


@pytest.mark.asyncio
async def test_save_rejects_duplicate_without_overwriting(tmp_path: Path) -> None:
    storage = UploadStorage(Settings(upload_dir=tmp_path))
    first = UploadFile(filename="same.py", file=BytesIO(b"first"))
    second = UploadFile(filename="same.py", file=BytesIO(b"second"))
    await storage.save(first)

    with pytest.raises(DuplicateUploadError):
        await storage.save(second)

    saved = storage.resolve(date.today().isoformat(), "same.py")
    assert saved.read_bytes() == b"first"


@pytest.mark.asyncio
async def test_save_removes_partial_file_when_upload_is_too_large(tmp_path: Path) -> None:
    storage = UploadStorage(Settings(upload_dir=tmp_path, max_upload_bytes=4))
    upload = UploadFile(filename="large.py", file=BytesIO(b"12345"))

    with pytest.raises(UploadTooLargeError):
        await storage.save(upload)

    assert not list(tmp_path.rglob("large.py"))


def test_resolve_rejects_missing_file(tmp_path: Path) -> None:
    storage = UploadStorage(Settings(upload_dir=tmp_path))

    with pytest.raises(FileNotFoundError):
        storage.resolve("2026-07-17", "missing.py")


def test_cleanup_expired_removes_old_files_only(tmp_path: Path) -> None:
    storage = UploadStorage(Settings(upload_dir=tmp_path, retention_days=7))
    old_dir = tmp_path / "2026-07-01"
    current_dir = tmp_path / date.today().isoformat()
    old_dir.mkdir(parents=True)
    current_dir.mkdir(parents=True)
    old_file = old_dir / "old.py"
    current_file = current_dir / "current.py"
    old_file.write_text("old", encoding="utf-8")
    current_file.write_text("current", encoding="utf-8")
    old_timestamp = (datetime.now() - timedelta(days=8)).timestamp()
    os.utime(old_file, (old_timestamp, old_timestamp))

    removed = storage.cleanup_expired()

    assert removed == 1
    assert not old_file.exists()
    assert current_file.exists()
