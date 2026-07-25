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

from hwp_live_preview_maintenance import maintain_live_previews  # noqa: E402
from hwp_live_preview_store import PreviewStore  # noqa: E402
from hwp_live_session import LiveHwpController  # noqa: E402


@final
class _MaintenanceProbe:
    cleanup_interval_seconds: float = 0.01

    def __init__(self) -> None:
        self.prune_thread_ids: list[int] = []

    def prune(self) -> None:
        self.prune_thread_ids.append(get_ident())


def test_preview_cleanup_runs_at_startup_periodically_and_after_shutdown() -> None:
    # Given
    probe = _MaintenanceProbe()

    async def exercise() -> int:
        event_loop_thread = get_ident()

        # When
        async with maintain_live_previews(probe):
            with anyio.fail_after(1):
                while len(probe.prune_thread_ids) < 2:
                    await anyio.sleep(0.01)
        return event_loop_thread

    event_loop_thread = anyio.run(exercise)

    # Then
    assert len(probe.prune_thread_ids) >= 3
    assert all(thread_id != event_loop_thread for thread_id in probe.prune_thread_ids)


def test_controller_exposes_one_preview_store_for_session_and_mcp_lifecycle() -> None:
    # Given
    controller = LiveHwpController()

    # When
    try:
        preview_store = controller.preview_store
    finally:
        controller.close()

    # Then
    assert isinstance(preview_store, PreviewStore)
    assert controller.preview_store is preview_store
