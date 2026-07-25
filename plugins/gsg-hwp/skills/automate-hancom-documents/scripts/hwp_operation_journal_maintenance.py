from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Protocol

from anyio import create_task_group, sleep, to_thread


class JournalMaintenanceTarget(Protocol):
    @property
    def cleanup_interval_seconds(self) -> float: ...

    def prune(self) -> object: ...


async def _prune(journal: JournalMaintenanceTarget) -> None:
    _ = await to_thread.run_sync(journal.prune)


async def _prune_periodically(journal: JournalMaintenanceTarget) -> None:
    while True:
        await sleep(journal.cleanup_interval_seconds)
        await _prune(journal)


@asynccontextmanager
async def maintain_operation_journal(
    journal: JournalMaintenanceTarget,
) -> AsyncGenerator[None]:
    await _prune(journal)
    async with create_task_group() as tasks:
        _ = tasks.start_soon(_prune_periodically, journal)
        try:
            yield
        finally:
            tasks.cancel_scope.cancel()
