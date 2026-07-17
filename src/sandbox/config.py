import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_upload_dir() -> Path:
    return Path("uploads").resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    upload_dir: Path = field(default_factory=_default_upload_dir)
    api_key: str = ""
    max_upload_bytes: int = 10 * 1024 * 1024
    max_output_bytes: int = 1024 * 1024
    max_timeout_seconds: int = 30
    max_concurrent: int = 4
    max_waiting: int = 20
    queue_wait_seconds: float = 5.0
    retention_days: int = 7
    cleanup_interval_seconds: float = 3600.0
    process_cpu_seconds: int = 30
    process_memory_bytes: int = 1536 * 1024 * 1024
    process_count_limit: int = 32
    process_file_bytes: int = 20 * 1024 * 1024
    process_open_files: int = 64
    terminate_grace_seconds: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "upload_dir", Path(self.upload_dir).resolve())
        positive_fields = (
            "max_upload_bytes",
            "max_output_bytes",
            "max_timeout_seconds",
            "max_concurrent",
            "queue_wait_seconds",
            "retention_days",
            "cleanup_interval_seconds",
            "process_cpu_seconds",
            "process_memory_bytes",
            "process_count_limit",
            "process_file_bytes",
            "process_open_files",
            "terminate_grace_seconds",
        )
        for name in positive_fields:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if self.max_waiting < 0:
            raise ValueError("max_waiting must be zero or greater")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            upload_dir=Path(os.getenv("SANDBOX_UPLOAD_DIR", "uploads")),
            api_key=os.getenv("SANDBOX_API_KEY", ""),
            max_upload_bytes=_env_int("SANDBOX_MAX_UPLOAD_BYTES", 10 * 1024 * 1024),
            max_output_bytes=_env_int("SANDBOX_MAX_OUTPUT_BYTES", 1024 * 1024),
            max_timeout_seconds=_env_int("SANDBOX_MAX_TIMEOUT_SECONDS", 30),
            max_concurrent=_env_int("SANDBOX_MAX_CONCURRENT", 4),
            max_waiting=_env_int("SANDBOX_MAX_WAITING", 20),
            queue_wait_seconds=_env_float("SANDBOX_QUEUE_WAIT_SECONDS", 5.0),
            retention_days=_env_int("SANDBOX_RETENTION_DAYS", 7),
            cleanup_interval_seconds=_env_float("SANDBOX_CLEANUP_INTERVAL_SECONDS", 3600.0),
            process_cpu_seconds=_env_int("SANDBOX_PROCESS_CPU_SECONDS", 30),
            process_memory_bytes=_env_int("SANDBOX_PROCESS_MEMORY_BYTES", 1536 * 1024 * 1024),
            process_count_limit=_env_int("SANDBOX_PROCESS_COUNT_LIMIT", 32),
            process_file_bytes=_env_int("SANDBOX_PROCESS_FILE_BYTES", 20 * 1024 * 1024),
            process_open_files=_env_int("SANDBOX_PROCESS_OPEN_FILES", 64),
            terminate_grace_seconds=_env_float("SANDBOX_TERMINATE_GRACE_SECONDS", 1.0),
        )


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))
