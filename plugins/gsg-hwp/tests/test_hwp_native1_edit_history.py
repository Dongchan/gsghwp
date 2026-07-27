from __future__ import annotations

# pyright: reportPrivateUsage=false

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "automate-hancom-documents" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hwp_live_edit_history_runtime as runtime  # noqa: E402
from hwp_live_edit_history import DocumentCheckpoint  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    NativeActionRequest,
    RestoreDocumentFileCommand,
)
from hwp_live_native_action_results import NativeActionResult  # noqa: E402
from hwp_live_rot import HwpDocumentCandidate  # noqa: E402


class _DirectApplicationForbidden:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"direct HWP COM access is forbidden: {name}")


def test_checkpoint_restore_uses_only_the_native_file_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    full_name = str(tmp_path / "document.hwp")
    checkpoint_path = tmp_path / "before.hwp-checkpoint"
    payload = b"GSG_HWP_ENCODED_BLOCK_V1\nencoded-hwp-document"
    _ = checkpoint_path.write_bytes(payload)
    checkpoint = DocumentCheckpoint(
        path=checkpoint_path,
        bytes=len(payload),
        page_count=4,
    )
    candidate = cast(
        HwpDocumentCandidate,
        cast(
            object,
            SimpleNamespace(
                window_handle=701,
                application=_DirectApplicationForbidden(),
            ),
        ),
    )
    calls: list[tuple[int, NativeActionRequest, int]] = []

    def execute(
        window_handle: int,
        request: NativeActionRequest,
        *,
        minimum_version: int,
    ) -> NativeActionResult:
        calls.append((window_handle, request, minimum_version))
        return NativeActionResult(
            commands_executed=1,
            actions_executed=4,
            text_insertions=0,
            image_insertions=0,
            elapsed_microseconds=37,
            created_control_ids=(),
        )

    monkeypatch.setattr(runtime, "execute_native_actions", execute)
    monkeypatch.setattr(
        runtime,
        "read_native_snapshot",
        lambda _window_handle: SimpleNamespace(
            document_id=7,
            full_name=full_name,
            page_count=4,
        ),
    )

    commands, elapsed = runtime._restore_checkpoint(
        candidate,
        7,
        full_name,
        checkpoint,
    )

    assert (commands, elapsed) == (1, 37)
    assert calls == [
        (
            701,
            NativeActionRequest(
                7,
                full_name,
                (RestoreDocumentFileCommand(checkpoint_path, 4),),
            ),
            12,
        )
    ]


def test_native_restore_command_owns_file_loading_and_rollback() -> None:
    native = ROOT / "addon" / "HancomLiveBridgeNative"
    protocol = (native / "ActionProtocol.cpp").read_text(encoding="utf-8")
    dispatch = (native / "ActionDispatch.cpp").read_text(encoding="utf-8")
    lifecycle = (native / "ActionLifecycle.cpp").read_text(encoding="utf-8")

    assert "RESTORE_DOCUMENT_FILE" in protocol
    assert "CommandKind::RestoreDocumentFile" in dispatch
    assert "RestoreDocumentFile" in lifecycle
    assert "DOCUMENT_CHECKPOINT_ROLLBACK" in lifecycle
    assert "SetTextFile" in lifecycle
