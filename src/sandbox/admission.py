import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class SandboxBusyError(Exception):
    pass


class ExecutionGate:
    def __init__(self, max_active: int, max_waiting: int, wait_timeout: float) -> None:
        self._max_active = max_active
        self._max_waiting = max_waiting
        self._wait_timeout = wait_timeout
        self._active = 0
        self._waiting = 0
        self._condition = asyncio.Condition()

    @property
    def active(self) -> int:
        return self._active

    @property
    def waiting(self) -> int:
        return self._waiting

    async def _acquire(self) -> None:
        async with self._condition:
            if self._active < self._max_active:
                self._active += 1
                return
            if self._waiting >= self._max_waiting:
                raise SandboxBusyError("execution queue is full")

            self._waiting += 1
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._active < self._max_active),
                    timeout=self._wait_timeout,
                )
                self._active += 1
            except TimeoutError:
                raise SandboxBusyError("execution queue wait timed out") from None
            finally:
                self._waiting -= 1

    async def _release(self) -> None:
        async with self._condition:
            self._active -= 1
            self._condition.notify(1)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self._acquire()
        try:
            yield
        finally:
            await self._release()
