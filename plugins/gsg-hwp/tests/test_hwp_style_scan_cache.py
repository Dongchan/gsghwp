from __future__ import annotations

# pyright: reportPrivateUsage=false

import sys
from pathlib import Path
from typing import cast, final


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_contract import DocumentStyle, DocumentStyleList  # noqa: E402
from hwp_live_bridge import HancomBridge  # noqa: E402
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_live_style_cache import StyleInspectionCache  # noqa: E402
from hwp_live_windows import WindowStateReader  # noqa: E402


@final
class _StyleController:
    def __init__(self) -> None:
        self.cache = StyleInspectionCache()
        self.scans = 0
        self.state_token = ""

    def set_style_state_token(self, session_id: str, state_token: str) -> None:
        assert session_id == "session-1"
        self.state_token = state_token

    def styles(self, session_id: str) -> DocumentStyleList:
        assert session_id == "session-1"

        def scan() -> DocumentStyleList:
            self.scans += 1
            return DocumentStyleList(
                styles=(DocumentStyle(style_id=self.scans, name="표타이틀"),)
            )

        return self.cache.resolve(
            17,
            "C:/documents/sample.hwp",
            self.state_token,
            scan,
        )

    def restore_activation(self) -> None:
        return

    def close(self) -> None:
        return


@final
class _Signal:
    def __init__(self) -> None:
        self.value = 0

    def sequence(self) -> int:
        return self.value

    def start(self, process_id: int, moniker_name: str | None = None) -> None:
        _ = process_id, moniker_name

    def wait(self, after_sequence: int, timeout_seconds: float) -> int:
        _ = after_sequence, timeout_seconds
        return self.value

    def stop(self) -> None:
        return


def test_style_cache_reuses_one_scan_for_same_document_and_state_token() -> None:
    cache = StyleInspectionCache()
    scans = 0

    def scan() -> DocumentStyleList:
        nonlocal scans
        scans += 1
        return DocumentStyleList(styles=(DocumentStyle(style_id=4, name="표타이틀"),))

    results = tuple(
        cache.resolve(17, "C:/documents/sample.hwp", "a" * 64, scan) for _ in range(11)
    )

    assert scans == 1
    assert all(result == results[0] for result in results)
    assert cache.metrics.hits == 10
    assert cache.metrics.misses == 1


def test_style_cache_invalidates_on_token_document_and_explicit_clear() -> None:
    cache = StyleInspectionCache()
    scans = 0

    def scan() -> DocumentStyleList:
        nonlocal scans
        scans += 1
        return DocumentStyleList(
            styles=(DocumentStyle(style_id=scans, name=f"style-{scans}"),)
        )

    first = cache.resolve(17, "C:/documents/sample.hwp", "a" * 64, scan)
    changed_token = cache.resolve(17, "C:/documents/sample.hwp", "b" * 64, scan)
    changed_document = cache.resolve(
        18,
        "C:/documents/other.hwp",
        "b" * 64,
        scan,
    )
    cache.clear()
    after_clear = cache.resolve(18, "C:/documents/other.hwp", "b" * 64, scan)

    assert tuple(
        result.styles[0].style_id
        for result in (first, changed_token, changed_document, after_clear)
    ) == (1, 2, 3, 4)
    assert scans == 4
    assert cache.metrics.hits == 0
    assert cache.metrics.misses == 4


def test_bridge_reuses_style_scan_until_event_or_local_mutation() -> None:
    controller = _StyleController()
    signal = _Signal()
    bridge = HancomBridge(
        cast(LiveHwpController, cast(object, controller)),
        window_reader=cast(WindowStateReader, object()),
    )
    bridge._events[101] = signal
    bridge._session_processes["session-1"] = 101
    bridge._style_revisions["session-1"] = 0

    try:
        results = tuple(bridge.styles("session-1") for _ in range(11))
        assert controller.scans == 1
        assert all(result == results[0] for result in results)

        signal.value += 1
        _ = bridge.styles("session-1")
        assert controller.scans == 2

        _ = bridge._call_mutation(lambda: None, session_id="session-1")
        _ = bridge.styles("session-1")
        assert controller.scans == 3
    finally:
        bridge.close()
