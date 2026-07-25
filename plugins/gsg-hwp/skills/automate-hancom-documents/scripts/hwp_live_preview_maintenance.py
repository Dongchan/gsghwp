from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Protocol

from anyio import CancelScope, create_task_group, sleep, to_thread

from hwp_live_preview_retention import PreviewPruneReport


class PreviewMaintenanceTarget(Protocol):
    @property
    def cleanup_interval_seconds(self) -> float: ...

    def prune(self) -> PreviewPruneReport | None: ...


async def _prune(previews: PreviewMaintenanceTarget) -> None:
    _ = await to_thread.run_sync(previews.prune)


async def _prune_periodically(previews: PreviewMaintenanceTarget) -> None:
    while True:
        await sleep(previews.cleanup_interval_seconds)
        await _prune(previews)


@asynccontextmanager
async def maintain_live_previews(
    previews: PreviewMaintenanceTarget,
) -> AsyncGenerator[None]:
    await _prune(previews)
    try:
        async with create_task_group() as tasks:
            _ = tasks.start_soon(_prune_periodically, previews)
            try:
                yield
            finally:
                tasks.cancel_scope.cancel()
    finally:
        with CancelScope(shield=True):
            await _prune(previews)
