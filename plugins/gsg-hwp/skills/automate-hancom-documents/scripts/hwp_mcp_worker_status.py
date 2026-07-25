from __future__ import annotations

from dataclasses import replace

from hwp_mcp_worker_protocol import WorkerStatus


def supervisor_status(
    snapshot: WorkerStatus,
    source_hash: str,
    *,
    restarting: bool,
    busy: bool,
    reload_requested: bool,
) -> WorkerStatus:
    if restarting:
        worker_state = "unknown"
        reload_state = "unknown"
    else:
        worker_state = "busy" if busy else "idle"
        reload_state = "deferred" if reload_requested else snapshot.reload_state
    return replace(
        snapshot,
        source_hash=source_hash,
        worker_state=worker_state,
        reload_state=reload_state,
    )
