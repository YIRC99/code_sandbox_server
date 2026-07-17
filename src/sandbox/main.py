import asyncio
import hmac
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from .admission import ExecutionGate, SandboxBusyError
from .config import Settings
from .executor import SandboxExecutor
from .storage import (
    DuplicateUploadError,
    InvalidPathError,
    UploadStorage,
    UploadTooLargeError,
)

logger = logging.getLogger("sandbox")


class ExecuteRequest(BaseModel):
    date: str = Field(description="Upload date in YYYY-MM-DD format")
    filename: str = Field(description="Uploaded Python filename")
    timeout: float | None = Field(default=None, gt=0, description="Timeout in seconds")


class ExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    status: str


async def _cleanup_loop(storage: UploadStorage, interval: float) -> None:
    while True:
        try:
            removed = await asyncio.to_thread(storage.cleanup_expired)
            if removed:
                logger.info("Removed %s expired sandbox uploads", removed)
        except Exception:
            logger.exception("Sandbox upload cleanup failed")
        await asyncio.sleep(interval)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        cleanup_task = asyncio.create_task(
            _cleanup_loop(application.state.storage, resolved_settings.cleanup_interval_seconds)
        )
        try:
            yield
        finally:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task

    application = FastAPI(
        title="Python Code Sandbox",
        description="Resource-bounded service for running AI-generated Python code.",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.storage = UploadStorage(resolved_settings)
    application.state.executor = SandboxExecutor(resolved_settings)
    application.state.gate = ExecutionGate(
        max_active=resolved_settings.max_concurrent,
        max_waiting=resolved_settings.max_waiting,
        wait_timeout=resolved_settings.queue_wait_seconds,
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    async def require_api_key(
        provided_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> None:
        expected_key = resolved_settings.api_key
        if expected_key and (
            provided_key is None or not hmac.compare_digest(provided_key, expected_key)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid API key",
            )

    authenticated = Depends(require_api_key)

    @application.get("/health", status_code=status.HTTP_200_OK)
    async def health_check() -> dict[str, int | str]:
        return {
            "status": "healthy",
            "active_executions": application.state.gate.active,
            "waiting_executions": application.state.gate.waiting,
        }

    @application.post("/upload", status_code=status.HTTP_201_CREATED)
    async def upload_file(
        file: Annotated[UploadFile, File()],
        request: Request,
        _: Annotated[None, authenticated],
    ) -> dict[str, str]:
        try:
            saved = await application.state.storage.save(file)
        except InvalidPathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except DuplicateUploadError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except UploadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Upload failed request_id=%s", request.state.request_id)
            raise HTTPException(status_code=500, detail="upload failed") from exc
        finally:
            await file.close()
        return {"date": saved.date, "filename": saved.filename}

    @application.post("/execute", response_model=ExecuteResponse)
    async def execute_code(
        body: ExecuteRequest,
        request: Request,
        _: Annotated[None, authenticated],
    ) -> ExecuteResponse:
        timeout = body.timeout if body.timeout is not None else min(
            10, resolved_settings.max_timeout_seconds
        )
        if timeout > resolved_settings.max_timeout_seconds:
            raise HTTPException(
                status_code=400,
                detail=f"timeout cannot exceed {resolved_settings.max_timeout_seconds} seconds",
            )
        try:
            script = application.state.storage.resolve(body.date, body.filename)
        except InvalidPathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="uploaded file not found") from exc

        logger.info(
            "Execution requested request_id=%s date=%s filename=%s timeout=%s",
            request.state.request_id,
            body.date,
            body.filename,
            timeout,
        )
        try:
            async with application.state.gate.slot():
                result = await application.state.executor.execute(script, timeout)
        except SandboxBusyError as exc:
            raise HTTPException(
                status_code=429,
                detail="sandbox is busy; retry later",
                headers={"Retry-After": "1"},
            ) from exc
        except Exception as exc:
            logger.exception(
                "Execution failed request_id=%s filename=%s",
                request.state.request_id,
                body.filename,
            )
            raise HTTPException(status_code=500, detail="sandbox execution failed") from exc

        logger.info(
            "Execution finished request_id=%s filename=%s status=%s exit_code=%s",
            request.state.request_id,
            body.filename,
            result.status,
            result.exit_code,
        )
        return ExecuteResponse(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            status=result.status,
        )

    return application


app = create_app()
