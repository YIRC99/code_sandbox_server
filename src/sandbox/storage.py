import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath

from fastapi import UploadFile
from loguru import logger

from .config import Settings

_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}\.py$")
_UPLOAD_CHUNK_BYTES = 64 * 1024
_WINDOWS_INVALID_SEGMENT_CHARACTERS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
    | {f"COM{index}" for index in "¹²³"}
    | {f"LPT{index}" for index in "¹²³"}
)


class StorageError(Exception):
    pass


class InvalidPathError(StorageError):
    pass


class DuplicateUploadError(StorageError):
    pass


class UploadTooLargeError(StorageError):
    pass


class DataFileTooLargeError(StorageError):
    pass


@dataclass(frozen=True, slots=True)
class SavedUpload:
    date: str
    filename: str


@dataclass(frozen=True, slots=True)
class SavedDataFile:
    data_file: str
    size_bytes: int


def validate_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        logger.warning(f"提供了无效的日期格式：{value}")
        raise InvalidPathError("date must use YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        logger.warning(f"日期格式不一致：{value}")
        raise InvalidPathError("date must use YYYY-MM-DD format")
    return value


def validate_filename(value: str | None) -> str:
    if not value or not _FILENAME_PATTERN.fullmatch(value):
        logger.warning(f"尝试使用不安全或无效的 Python 文件名：{value}")
        raise InvalidPathError("filename must be a safe Python basename")
    return value


def validate_data_path_segments(value: str) -> None:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        logger.warning(f"data_file 中包含不安全的控制字符：{repr(value)}")
        raise InvalidPathError("data_file contains an unsafe path segment")

    for segment in re.split(r"[/\\]", value):
        if not segment:
            continue
        if any(character in _WINDOWS_INVALID_SEGMENT_CHARACTERS for character in segment):
            logger.warning(f"data_file 路径段中包含不安全的 Windows 字符：{segment}")
            raise InvalidPathError("data_file contains an unsafe path segment")
        if segment != segment.rstrip(" ."):
            logger.warning(f"data_file 路径段包含末尾点号或空格：{segment}")
            raise InvalidPathError("data_file contains an unsafe path segment")
        device_name = segment.split(".", maxsplit=1)[0].rstrip(" .").upper()
        if device_name in _WINDOWS_RESERVED_NAMES:
            logger.warning(f"data_file 路径段包含保留设备名：{device_name}")
            raise InvalidPathError("data_file contains an unsafe path segment")


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
        logger.info(f"保存上传文件：文件名={filename} 日期={date_string}")
        try:
            with target.open("xb") as destination:
                created = True
                total = 0
                while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
                    total += len(chunk)
                    if total > self._max_upload_bytes:
                        logger.warning(
                            f"上传文件大小超出限制 ({total} > "
                            f"{self._max_upload_bytes} 字节)：{filename}"
                        )
                        raise UploadTooLargeError(f"upload exceeds {self._max_upload_bytes} bytes")
                    destination.write(chunk)
        except FileExistsError as exc:
            logger.warning(f"重复上传被拒绝：日期={date_string} 文件名={filename}")
            raise DuplicateUploadError("a file with this name already exists today") from exc
        except Exception:
            if created:
                target.unlink(missing_ok=True)
            raise
        logger.info(f"文件上传保存成功：日期={date_string} 文件名={filename} ({total} 字节)")
        return SavedUpload(date=date_string, filename=filename)

    def resolve(self, date_string: str, filename: str) -> Path:
        safe_date = validate_date(date_string)
        safe_filename = validate_filename(filename)
        candidate = self._root / safe_date / safe_filename
        resolved = candidate.resolve()
        logger.debug(f"解析上传脚本路径：日期={date_string} 文件名={filename}")
        if not resolved.is_relative_to(self._root):
            logger.error(f"上传脚本路径越界：日期={date_string} 文件名={filename}")
            raise InvalidPathError("resolved path is outside the upload directory")
        if not resolved.is_file():
            logger.warning(f"未找到上传脚本：日期={date_string} 文件名={filename}")
            raise FileNotFoundError(safe_filename)
        return resolved

    def cleanup_expired(self) -> int:
        if not self._root.exists():
            return 0
        cutoff = datetime.now() - timedelta(days=self._retention_days)
        removed = 0
        logger.debug(f"正在清理过期上传文件 (截止时间={cutoff.isoformat()})...")
        for path in self._root.glob("*/*.py"):
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime)
                if modified < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
                    logger.info(
                        f"已清理过期上传脚本：日期={path.parent.name} "
                        f"文件名={path.name} 修改时间={modified}"
                    )
            except FileNotFoundError:
                continue
        try:
            directories = list(self._root.iterdir())
        except FileNotFoundError:
            return removed
        cutoff_date = cutoff.date()
        for directory in directories:
            try:
                directory_date = date.fromisoformat(directory.name)
            except ValueError:
                continue
            if directory_date.isoformat() != directory.name or directory_date >= cutoff_date:
                continue
            try:
                directory.rmdir()
                logger.info(f"已清理过期的空上传目录：{directory}")
            except OSError:
                pass
        if removed > 0:
            logger.info(f"文件清理周期完成。共计清理文件数：{removed}")
        return removed


class DataFileStorage:
    def __init__(self, settings: Settings) -> None:
        self._root = settings.data_dir
        self._upload_root = settings.data_upload_dir
        self._max_data_file_bytes = settings.max_data_file_bytes
        self._retention_days = settings.retention_days

    async def save(self, upload: UploadFile) -> SavedDataFile:
        filename = upload.filename
        if not filename or PureWindowsPath(filename).suffix.lower() != ".csv":
            raise InvalidPathError("file must use the .csv extension")

        date_string = date.today().isoformat()
        target_dir = self._upload_root / date_string
        target_dir.mkdir(parents=True, exist_ok=True)
        generated_name = f"{uuid.uuid4()}.csv"
        target = target_dir / generated_name
        part = target.with_suffix(f"{target.suffix}.part")
        total = 0
        try:
            with part.open("xb") as destination:
                while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
                    total += len(chunk)
                    if total > self._max_data_file_bytes:
                        raise DataFileTooLargeError(
                            f"data file upload exceeds {self._max_data_file_bytes} bytes"
                        )
                    destination.write(chunk)
            part.replace(target)
        except Exception:
            part.unlink(missing_ok=True)
            raise

        data_file = f"uploaded/{date_string}/{generated_name}"
        logger.info(f"行情数据文件上传成功：data_file={data_file} size_bytes={total}")
        return SavedDataFile(data_file=data_file, size_bytes=total)

    def resolve(self, data_file: str) -> Path:
        validate_data_path_segments(data_file)
        windows_path = PureWindowsPath(data_file)
        posix_path = PurePosixPath(data_file)
        relative_path = Path(data_file)
        path_parts = windows_path.parts
        if (
            not data_file
            or windows_path.drive
            or windows_path.is_absolute()
            or posix_path.is_absolute()
            or relative_path.is_absolute()
            or any(part in {".", ".."} for part in path_parts)
            or relative_path.suffix.lower() != ".csv"
        ):
            logger.warning(f"请求的数据文件路径结构无效：{data_file}")
            raise InvalidPathError("data_file must be a relative CSV path")

        if path_parts and path_parts[0] == "uploaded":
            if len(path_parts) == 1:
                raise InvalidPathError("uploaded data_file path is incomplete")
            root = self._upload_root
            relative_path = Path(*path_parts[1:])
        else:
            root = self._root
            relative_path = Path(*path_parts)

        try:
            resolved = (root / relative_path).resolve()
        except (OSError, ValueError) as exc:
            logger.warning(f"无法解析相对数据文件路径：{data_file}")
            raise InvalidPathError("data_file must be a relative CSV path") from exc
        if not resolved.is_relative_to(root):
            logger.error(f"数据文件路径越界：data_file={data_file}")
            raise InvalidPathError("data_file must stay within the data directory")
        if not resolved.is_file():
            logger.warning(f"未找到 CSV 数据文件：data_file={data_file}")
            raise FileNotFoundError(data_file)
        try:
            data_size = resolved.stat().st_size
        except FileNotFoundError as exc:
            logger.warning(f"读取 CSV 文件信息时文件消失：data_file={data_file}")
            raise FileNotFoundError(data_file) from exc
        if data_size > self._max_data_file_bytes:
            logger.warning(
                f"数据文件大小超过限制：data_file={data_file} "
                f"size_bytes={data_size} limit_bytes={self._max_data_file_bytes}"
            )
            raise DataFileTooLargeError("data file exceeds configured size limit")

        logger.debug(f"成功解析数据文件：data_file={data_file} size_bytes={data_size}")
        return resolved

    def cleanup_expired(self) -> int:
        if not self._upload_root.exists():
            return 0
        cutoff = datetime.now() - timedelta(days=self._retention_days)
        removed = 0
        for path in self._upload_root.glob("*/*"):
            if not path.is_file() or (
                path.suffix.lower() != ".csv" and not path.name.lower().endswith(".csv.part")
            ):
                continue
            try:
                if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
                    logger.info(
                        f"已清理过期行情数据文件：date={path.parent.name} filename={path.name}"
                    )
            except FileNotFoundError:
                continue

        cutoff_date = cutoff.date()
        try:
            directories = list(self._upload_root.iterdir())
        except FileNotFoundError:
            return removed
        for directory in directories:
            if not directory.is_dir():
                continue
            try:
                directory_date = date.fromisoformat(directory.name)
            except ValueError:
                continue
            if directory_date.isoformat() != directory.name or directory_date >= cutoff_date:
                continue
            try:
                directory.rmdir()
            except OSError:
                pass
        return removed
