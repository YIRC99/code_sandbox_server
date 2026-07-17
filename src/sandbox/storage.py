import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import UploadFile

from .config import Settings

_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}\.py$")
_UPLOAD_CHUNK_BYTES = 64 * 1024


class StorageError(Exception):
    pass


class InvalidPathError(StorageError):
    pass


class DuplicateUploadError(StorageError):
    pass


class UploadTooLargeError(StorageError):
    pass


@dataclass(frozen=True, slots=True)
class SavedUpload:
    date: str
    filename: str


def validate_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidPathError("date must use YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise InvalidPathError("date must use YYYY-MM-DD format")
    return value


def validate_filename(value: str | None) -> str:
    if not value or not _FILENAME_PATTERN.fullmatch(value):
        raise InvalidPathError("filename must be a safe Python basename")
    return value


class UploadStorage:
    def __init__(self, settings: Settings) -> None:
        self._root = settings.upload_dir
        self._max_upload_bytes = settings.max_upload_bytes
        self._retention_days = settings.retention_days

    async def save(self, upload: UploadFile) -> SavedUpload:
        filename = validate_filename(upload.filename)
        date_string = date.today().isoformat()
        target_dir = self._root / date_string
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        created = False
        try:
            with target.open("xb") as destination:
                created = True
                total = 0
                while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
                    total += len(chunk)
                    if total > self._max_upload_bytes:
                        raise UploadTooLargeError(
                            f"upload exceeds {self._max_upload_bytes} bytes"
                        )
                    destination.write(chunk)
        except FileExistsError as exc:
            raise DuplicateUploadError("a file with this name already exists today") from exc
        except Exception:
            if created:
                target.unlink(missing_ok=True)
            raise
        return SavedUpload(date=date_string, filename=filename)

    def resolve(self, date_string: str, filename: str) -> Path:
        safe_date = validate_date(date_string)
        safe_filename = validate_filename(filename)
        candidate = self._root / safe_date / safe_filename
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self._root):
            raise InvalidPathError("resolved path is outside the upload directory")
        if not resolved.is_file():
            raise FileNotFoundError(safe_filename)
        return resolved

    def cleanup_expired(self) -> int:
        if not self._root.exists():
            return 0
        cutoff = datetime.now() - timedelta(days=self._retention_days)
        removed = 0
        for path in self._root.glob("*/*.py"):
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime)
                if modified < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
            except FileNotFoundError:
                continue
        for directory in self._root.iterdir():
            if directory.is_dir():
                try:
                    directory.rmdir()
                except OSError:
                    pass
        return removed
