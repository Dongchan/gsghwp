from __future__ import annotations

import sys
from pathlib import Path
from threading import get_ident
from typing import final

import anyio


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_operation_journal_maintenance import (  # noqa: E402
    maintain_operation_journal,
)


@final
class _MaintenanceProbe:
    cleanup_interval_seconds: float = 0.01

    def __init__(self) -> None:
        self.prune_thread_ids: list[int] = []

    def prune(self) -> None:
        self.prune_thread_ids.append(get_ident())


def test_startup_and_periodic_pruning_run_outside_event_loop() -> None:
    probe = _MaintenanceProbe()

    async def exercise() -> int:
        event_loop_thread = get_ident()
        async with maintain_operation_journal(probe):
            with anyio.fail_after(1):
                while len(probe.prune_thread_ids) < 2:
                    await anyio.sleep(0.01)
        return event_loop_thread

    event_loop_thread = anyio.run(exercise)

    assert len(probe.prune_thread_ids) >= 2
    assert all(thread_id != event_loop_thread for thread_id in probe.prune_thread_ids)
