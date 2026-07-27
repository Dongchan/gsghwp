from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from time import monotonic
from typing import final


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_events import ChangeNotifier  # noqa: E402
from hwp_live_native_events import NativeHwpEventSignal  # noqa: E402


@final
class _Process:
    def __init__(self) -> None:
        self.stdin = StringIO()
        self.stdout = StringIO(
            '{"type":"ready","protocol":1,"moniker":"test","source_iid":"test"}\n'
        )
        self.stderr = StringIO()
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 1

    def kill(self) -> None:
        self.returncode = 1


def test_native_event_bridge_receives_target_process_id(tmp_path: Path) -> None:
    executable = tmp_path / "HancomEventBridge.exe"
    _ = executable.write_bytes(b"fixture")
    spawned: list[tuple[str, ...]] = []
    process = _Process()

    def spawn(command: tuple[str, ...]) -> _Process:
        spawned.append(command)
        return process

    signal = NativeHwpEventSignal(
        notifier=ChangeNotifier(),
        executable=executable,
        spawner=spawn,
    )

    signal.start(4321, "!HancomLiveBridge.test")
    signal.stop()

    assert spawned == [
        (
            str(executable),
            "--moniker",
            "!HancomLiveBridge.test",
            "--process-id",
            "4321",
        )
    ]


def test_native_save_events_preserve_before_and_after_kinds(tmp_path: Path) -> None:
    executable = tmp_path / "HancomEventBridge.exe"
    _ = executable.write_bytes(b"fixture")
    process = _Process()
    process.stdout = StringIO(
        "\n".join(
            (
                '{"type":"ready","protocol":1,"moniker":"test","source_iid":"test"}',
                (
                    '{"type":"event","event":"DocumentBeforeSave",'
                    '"dispid":8,"document_id":17}'
                ),
                (
                    '{"type":"event","event":"DocumentAfterSave",'
                    '"dispid":9,"document_id":17}'
                ),
                "",
            )
        )
    )

    def spawn(command: tuple[str, ...]) -> _Process:
        _ = command
        return process

    signal = NativeHwpEventSignal(
        notifier=ChangeNotifier(),
        executable=executable,
        spawner=spawn,
    )

    signal.start(4321, "!HancomLiveBridge.test")
    deadline = monotonic() + 1
    while signal.sequence() < 2 and monotonic() < deadline:
        _ = signal.wait(signal.sequence(), 0.01)
    events = signal.events_after(0)
    signal.stop()

    assert tuple(event.name for event in events) == (
        "DocumentBeforeSave",
        "DocumentAfterSave",
    )
    assert tuple(event.document_id for event in events) == (17, 17)
    assert events[0].sequence < events[1].sequence
