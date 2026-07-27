from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_bridge_contract import HancomWindowState
from hwp_live_windows import WindowStateReader


def _looks_like_hwp_main_window(window: HancomWindowState) -> bool:
    if window.class_name == "#32770":
        return False
    class_name = window.class_name.casefold()
    title = window.title.strip()
    return "hwp" in class_name or title == "Hwp" or title.endswith(" - 한글")


def _visible_child_texts(window: HancomWindowState) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            child.title.strip()
            for child in window.children
            if child.visible and child.title.strip()
        )
    )


def _looks_like_top_level_dialog(
    window: HancomWindowState,
    *,
    target_process_known: bool,
) -> bool:
    if not window.exists or not window.visible or window.class_name != "#32770":
        return False
    if target_process_known:
        return True
    return window.title.strip() == "Hwp" and bool(_visible_child_texts(window))


def _format_popup_diagnostic(
    *,
    target_process_known: bool,
    process_id: int,
    window_handle: int,
    owner_handle: int | None,
    class_name: str,
    texts: tuple[str, ...],
) -> str:
    ownership = "target_process" if target_process_known else "unverified"
    ownership_note = (
        "대상 프로세스 소유 확인"
        if target_process_known
        else "대상 프로세스 소유 및 한컴 관련성 미확인 외부 창 후보"
    )
    fields = [
        f"popup_ownership={ownership}",
        f"ownership_note={ownership_note}",
        f"PID={process_id}",
        f"HWND={window_handle}",
    ]
    if owner_handle is not None:
        fields.append(f"owner_HWND={owner_handle}")
    fields.extend((f"class={class_name}", f"text={' | '.join(texts[:8])}"))
    return ", ".join(fields)


def popup_diagnostic(
    windows: WindowStateReader,
    *,
    target_process_id: int,
) -> str | None:
    try:
        visible_windows = windows.list_visible_hwp_windows().windows
    except (HwpLiveError, OSError, RuntimeError):
        return None
    target_process_known = target_process_id > 0
    diagnostics: list[str] = []
    seen_dialog_handles: set[int] = set()
    for window in visible_windows:
        if target_process_known and window.process_id != target_process_id:
            continue
        top_level_dialog = _looks_like_top_level_dialog(
            window,
            target_process_known=target_process_known,
        )
        if (
            not target_process_known
            and not top_level_dialog
            and not _looks_like_hwp_main_window(window)
        ):
            continue
        if top_level_dialog and window.window_handle not in seen_dialog_handles:
            seen_dialog_handles.add(window.window_handle)
            diagnostics.append(
                _format_popup_diagnostic(
                    target_process_known=target_process_known,
                    process_id=window.process_id,
                    window_handle=window.window_handle,
                    owner_handle=None,
                    class_name=window.class_name,
                    texts=(window.title.strip(), *_visible_child_texts(window)),
                )
            )
        for dialog in window.dialogs:
            if (
                not dialog.visible
                or not dialog.modal
                or dialog.window_handle in seen_dialog_handles
            ):
                continue
            seen_dialog_handles.add(dialog.window_handle)
            texts = tuple(
                dict.fromkeys(
                    text.strip()
                    for text in (
                        dialog.title,
                        window.title,
                    )
                    if text.strip()
                )
            )
            diagnostics.append(
                _format_popup_diagnostic(
                    target_process_known=target_process_known,
                    process_id=window.process_id,
                    window_handle=dialog.window_handle,
                    owner_handle=dialog.owner_handle,
                    class_name=dialog.class_name,
                    texts=texts,
                )
            )
    return "; ".join(diagnostics) or None
