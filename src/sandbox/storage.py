import re
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
        logger.info(f"保存上传文件：文件名={filename} 日期={date_string} 目标路径={target}")
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
            logger.warning(f"重复上传被拒绝：文件已存在于 {target}")
            raise DuplicateUploadError("a file with this name already exists today") from exc
        except Exception:
            if created:
                target.unlink(missing_ok=True)
            raise
        logger.info(f"文件上传保存成功：{target} ({total} 字节)")
        return SavedUpload(date=date_string, filename=filename)

    def resolve(self, date_string: str, filename: str) -> Path:
        safe_date = validate_date(date_string)
        safe_filename = validate_filename(filename)
        candidate = self._root / safe_date / safe_filename
        resolved = candidate.resolve()
        logger.debug(f"解析上传脚本路径：请求=({date_string}, {filename}) -> 解析目标={resolved}")
        if not resolved.is_relative_to(self._root):
            logger.error(f"安全告警：解析后的上传路径 {resolved} 越界超出了上传根目录 {self._root}")
            raise InvalidPathError("resolved path is outside the upload directory")
        if not resolved.is_file():
            logger.warning(f"未找到上传脚本：日期={date_string} 文件名={filename} 路径={resolved}")
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
                    logger.info(f"已清理过期上传脚本：{path} (修改时间: {modified})")
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
        self._max_data_file_bytes = settings.max_data_file_bytes

    def resolve(self, data_file: str) -> Path:
        validate_data_path_segments(data_file)
        windows_path = PureWindowsPath(data_file)
        posix_path = PurePosixPath(data_file)
        relative_path = Path(data_file)
        if (
            not data_file
            or windows_path.drive
            or windows_path.is_absolute()
            or posix_path.is_absolute()
            or relative_path.is_absolute()
            or relative_path.suffix.lower() != ".csv"
        ):
            logger.warning(f"请求的数据文件路径结构无效：{data_file}")
            raise InvalidPathError("data_file must be a relative CSV path")

        try:
            resolved = (self._root / relative_path).resolve()
        except (OSError, ValueError) as exc:
            logger.warning(f"无法解析相对数据文件路径：{data_file}")
            raise InvalidPathError("data_file must be a relative CSV path") from exc
        if not resolved.is_relative_to(self._root):
            logger.error(f"安全告警：数据文件路径 {resolved} 越界超出了数据根目录 {self._root}")
            raise InvalidPathError("data_file must stay within the data directory")
        if not resolved.is_file():
            logger.warning(f"在解析路径处未找到 CSV 数据文件：{resolved}")
            raise FileNotFoundError(data_file)
        try:
            data_size = resolved.stat().st_size
        except FileNotFoundError as exc:
            logger.warning(f"CSV 数据文件缺失文件信息检测：{resolved}")
            raise FileNotFoundError(data_file) from exc
        if data_size > self._max_data_file_bytes:
            logger.warning(
                f"数据文件大小超过限制 ({data_size} > {self._max_data_file_bytes} 字节)：{resolved}"
            )
            raise DataFileTooLargeError("data file exceeds configured size limit")

        logger.debug(f"成功解析数据文件：{resolved} ({data_size} 字节)")
        return resolved
