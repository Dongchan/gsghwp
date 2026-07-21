from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_edit_history_policy import (  # noqa: E402
    should_capture_full_document_checkpoint,
)


def test_small_document_delete_uses_grouped_native_history(tmp_path: Path) -> None:
    document = tmp_path / "small.hwp"
    _ = document.write_bytes(b"HWP")

    assert not should_capture_full_document_checkpoint(str(document))
