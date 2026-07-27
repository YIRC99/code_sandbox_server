import os
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile

from sandbox.config import Settings
from sandbox.storage import (
    DataFileStorage,
    DataFileTooLargeError,
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


def test_cleanup_expired_only_removes_empty_expired_date_directories(tmp_path: Path) -> None:
    retention_days = 7
    storage = UploadStorage(Settings(upload_dir=tmp_path, retention_days=retention_days))
    today = date.today()
    current_dir = tmp_path / today.isoformat()
    retained_dir = tmp_path / (today - timedelta(days=retention_days - 1)).isoformat()
    expired_dir = tmp_path / (today - timedelta(days=retention_days + 1)).isoformat()
    invalid_dir = tmp_path / "not-a-date"
    for directory in (current_dir, retained_dir, expired_dir, invalid_dir):
        directory.mkdir()

    removed = storage.cleanup_expired()

    assert removed == 0
    assert current_dir.is_dir()
    assert retained_dir.is_dir()
    assert not expired_dir.exists()
    assert invalid_dir.is_dir()


@pytest.mark.asyncio
async def test_data_file_save_preserves_original_bytes_and_uses_server_filename(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "built-in"
    upload_dir = tmp_path / "data-files"
    original = b"\xef\xbb\xbfsecurity_id,issue_time\r\n123,2026-07-27\r\n"
    storage = DataFileStorage(
        Settings(data_dir=data_dir, data_upload_dir=upload_dir, max_data_file_bytes=1024)
    )
    upload = UploadFile(filename="client-name.csv", file=BytesIO(original))

    saved = await storage.save(upload)

    assert saved.data_file.startswith(f"uploaded/{date.today().isoformat()}/")
    assert saved.data_file.endswith(".csv")
    assert "client-name" not in saved.data_file
    assert saved.size_bytes == len(original)
    assert storage.resolve(saved.data_file).read_bytes() == original


@pytest.mark.asyncio
async def test_data_file_save_rejects_non_csv_without_creating_files(tmp_path: Path) -> None:
    upload_dir = tmp_path / "data-files"
    storage = DataFileStorage(Settings(data_upload_dir=upload_dir))
    upload = UploadFile(filename="quotes.txt", file=BytesIO(b"a,b\n1,2\n"))

    with pytest.raises(InvalidPathError):
        await storage.save(upload)

    assert not list(upload_dir.rglob("*"))


@pytest.mark.asyncio
async def test_data_file_save_removes_part_when_upload_is_too_large(tmp_path: Path) -> None:
    upload_dir = tmp_path / "data-files"
    storage = DataFileStorage(Settings(data_upload_dir=upload_dir, max_data_file_bytes=4))
    upload = UploadFile(filename="quotes.csv", file=BytesIO(b"12345"))

    with pytest.raises(DataFileTooLargeError):
        await storage.save(upload)

    assert not list(upload_dir.rglob("*.part"))
    assert not list(upload_dir.rglob("*.csv"))


@pytest.mark.asyncio
async def test_data_file_save_generates_unique_names(tmp_path: Path) -> None:
    storage = DataFileStorage(Settings(data_upload_dir=tmp_path / "data-files"))

    first = await storage.save(UploadFile(filename="same.csv", file=BytesIO(b"first")))
    second = await storage.save(UploadFile(filename="same.csv", file=BytesIO(b"second")))

    assert first.data_file != second.data_file
    assert storage.resolve(first.data_file).read_bytes() == b"first"
    assert storage.resolve(second.data_file).read_bytes() == b"second"


def test_data_file_resolve_supports_built_in_and_uploaded_roots(tmp_path: Path) -> None:
    data_dir = tmp_path / "built-in"
    upload_dir = tmp_path / "data-files"
    data_dir.mkdir()
    (data_dir / "test_data.csv").write_bytes(b"built-in")
    uploaded = upload_dir / "2026-07-27" / "abc.csv"
    uploaded.parent.mkdir(parents=True)
    uploaded.write_bytes(b"uploaded")
    storage = DataFileStorage(Settings(data_dir=data_dir, data_upload_dir=upload_dir))

    assert storage.resolve("test_data.csv").read_bytes() == b"built-in"
    assert storage.resolve("uploaded/2026-07-27/abc.csv").read_bytes() == b"uploaded"


@pytest.mark.parametrize(
    "data_file",
    [
        "uploaded/../test_data.csv",
        "uploaded/2026-07-27/../../test_data.csv",
        r"uploaded\..\test_data.csv",
    ],
)
def test_data_file_resolve_rejects_uploaded_path_traversal(
    tmp_path: Path,
    data_file: str,
) -> None:
    data_dir = tmp_path / "built-in"
    upload_dir = tmp_path / "data-files"
    data_dir.mkdir()
    upload_dir.mkdir()
    (data_dir / "test_data.csv").write_bytes(b"built-in")
    storage = DataFileStorage(Settings(data_dir=data_dir, data_upload_dir=upload_dir))

    with pytest.raises(InvalidPathError):
        storage.resolve(data_file)


def test_data_file_cleanup_only_removes_expired_uploaded_csv(tmp_path: Path) -> None:
    data_dir = tmp_path / "built-in"
    upload_dir = tmp_path / "data-files"
    data_dir.mkdir()
    built_in = data_dir / "test_data.csv"
    built_in.write_bytes(b"built-in")
    old_dir = upload_dir / "2026-07-01"
    current_dir = upload_dir / date.today().isoformat()
    old_dir.mkdir(parents=True)
    current_dir.mkdir(parents=True)
    old_file = old_dir / "old.csv"
    current_file = current_dir / "current.csv"
    old_file.write_bytes(b"old")
    current_file.write_bytes(b"current")
    old_timestamp = (datetime.now() - timedelta(days=8)).timestamp()
    os.utime(old_file, (old_timestamp, old_timestamp))
    storage = DataFileStorage(
        Settings(data_dir=data_dir, data_upload_dir=upload_dir, retention_days=7)
    )

    removed = storage.cleanup_expired()

    assert removed == 1
    assert not old_file.exists()
    assert current_file.exists()
    assert built_in.read_bytes() == b"built-in"
