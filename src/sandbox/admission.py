import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger


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
        logger.info(
            "执行门控初始化完成：最大并发槽位={} 最大等待队列={} 排队超时时间={:.2f}秒",
            max_active,
            max_waiting,
            wait_timeout,
        )

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
                logger.debug(
                    "立即获取到执行槽位：运行中={}/{} 排队中={}/{}",
                    self._active,
                    self._max_active,
                    self._waiting,
                    self._max_waiting,
                )
                return
            if self._waiting >= self._max_waiting:
                logger.warning(
                    "执行门控等待队列已满！拒绝请求：运行中={}/{} 排队中={}/{}",
                    self._active,
                    self._max_active,
                    self._waiting,
                    self._max_waiting,
                )
                raise SandboxBusyError("execution queue is full")

            self._waiting += 1
            logger.info(
                "执行门控并发槽位已满 ({}/{})。请求已进入队列，当前排队位置 {}/{}。",
                self._active,
                self._max_active,
                self._waiting,
                self._max_waiting,
            )
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._active < self._max_active),
                    timeout=self._wait_timeout,
                )
                self._active += 1
                logger.info(
                    "从队列中成功获取执行槽位：运行中={}/{} 排队中={}/{}",
                    self._active,
                    self._max_active,
                    self._waiting,
                    self._max_waiting,
                )
            except TimeoutError:
                logger.warning(
                    "排队等待超时 ({:.2f}秒)：运行中={}/{} 排队中={}/{}",
                    self._wait_timeout,
                    self._active,
                    self._max_active,
                    self._waiting,
                    self._max_waiting,
                )
                raise SandboxBusyError("execution queue wait timed out") from None
            finally:
                self._waiting -= 1

    async def _release(self) -> None:
        async with self._condition:
            self._active -= 1
            logger.debug(
                "执行槽位已释放：运行中={}/{} 排队中={}/{}",
                self._active,
                self._max_active,
                self._waiting,
                self._max_waiting,
            )
            self._condition.notify(1)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self._acquire()
        try:
            yield
        finally:
            await self._release()
