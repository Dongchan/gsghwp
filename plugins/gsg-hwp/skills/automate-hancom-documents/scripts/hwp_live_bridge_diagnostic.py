from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_windows import WindowStateReader


def popup_diagnostic(windows: WindowStateReader) -> str | None:
    try:
        visible_windows = windows.list_visible_hwp_windows().windows
    except (HwpLiveError, OSError, RuntimeError):
        return None
    diagnostics: list[str] = []
    for window in visible_windows:
        if window.class_name != "#32770" and not window.dialogs:
            continue
        texts = tuple(
            dict.fromkeys(
                text.strip()
                for text in (
                    window.title,
                    *(child.title for child in window.children),
                )
                if text.strip()
            )
        )
        detail = " | ".join(texts[:8])
        diagnostics.append(
            f"HWND={window.window_handle}, class={window.class_name}, text={detail}"
        )
    return "; ".join(diagnostics) or None
