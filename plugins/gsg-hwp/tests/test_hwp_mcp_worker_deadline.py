from __future__ import annotations

import sys
from pathlib import Path

import anyio
import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_mcp_worker_deadline import call_worker_before_deadline  # noqa: E402
from hwp_mcp_worker_protocol import (  # noqa: E402
    HwpWorkerCallTimeout,
    WorkerRequest,
)


async def _time_out_while_waiting_to_enter_queue() -> None:
    send, receive = anyio.create_memory_object_stream[WorkerRequest](0)
    async with send, receive:
        with pytest.raises(HwpWorkerCallTimeout) as timeout:
            _ = await call_worker_before_deadline(
                send,
                "hwp_inspect",
                {},
                0.01,
                mutation=False,
            )
    assert timeout.value.dispatched is False
    assert timeout.value.started is False
    assert timeout.value.reconcile_required is False
    assert "phase=queue_wait" in str(timeout.value)
    assert "retry_safe=true" in str(timeout.value)


def test_total_deadline_includes_request_queue_wait() -> None:
    anyio.run(_time_out_while_waiting_to_enter_queue)
