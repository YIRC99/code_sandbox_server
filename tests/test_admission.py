import asyncio

import pytest

from sandbox.admission import ExecutionGate, SandboxBusyError


@pytest.mark.asyncio
async def test_slot_tracks_active_execution() -> None:
    gate = ExecutionGate(max_active=1, max_waiting=1, wait_timeout=0.1)

    async with gate.slot():
        assert gate.active == 1

    assert gate.active == 0


@pytest.mark.asyncio
async def test_waiter_enters_after_active_slot_is_released() -> None:
    gate = ExecutionGate(max_active=1, max_waiting=1, wait_timeout=0.2)
    entered = asyncio.Event()

    async def wait_for_slot() -> None:
        async with gate.slot():
            entered.set()

    async with gate.slot():
        waiter = asyncio.create_task(wait_for_slot())
        for _ in range(10):
            if gate.waiting == 1:
                break
            await asyncio.sleep(0)
        assert gate.waiting == 1
        assert not entered.is_set()

    await waiter
    assert entered.is_set()
    assert gate.active == 0


@pytest.mark.asyncio
async def test_queue_overflow_is_rejected_immediately() -> None:
    gate = ExecutionGate(max_active=1, max_waiting=1, wait_timeout=0.2)

    async def queued_request() -> None:
        async with gate.slot():
            return None

    async with gate.slot():
        waiter = asyncio.create_task(queued_request())
        for _ in range(10):
            if gate.waiting == 1:
                break
            await asyncio.sleep(0)

        with pytest.raises(SandboxBusyError, match="queue is full"):
            async with gate.slot():
                pass

    await waiter


@pytest.mark.asyncio
async def test_wait_timeout_is_rejected_and_waiter_count_recovers() -> None:
    gate = ExecutionGate(max_active=1, max_waiting=1, wait_timeout=0.01)

    async with gate.slot():
        with pytest.raises(SandboxBusyError, match="wait timed out"):
            async with gate.slot():
                pass
        assert gate.waiting == 0

