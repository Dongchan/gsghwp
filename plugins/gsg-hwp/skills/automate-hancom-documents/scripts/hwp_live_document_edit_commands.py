from __future__ import annotations

from hwp_live_native_action_models import (
    DeleteControlCommand,
    IntegerValue,
    NativeActionCommand,
    NativePageControl,
    NativeSetter,
    ParameterActionCommand,
    TextValue,
)


def build_delete_page_commands(page: int) -> tuple[NativeActionCommand, ...]:
    if isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    return (
        ParameterActionCommand(
            "DeletePage",
            "HDeletePage",
            setters=(
                NativeSetter("Range", IntegerValue(2)),
                NativeSetter("RangeCustom", TextValue(str(page))),
                NativeSetter("UsingPagenum", IntegerValue(0)),
            ),
        ),
    )


def build_delete_control_commands(
    controls: tuple[NativePageControl, ...],
) -> tuple[NativeActionCommand, ...]:
    if not controls:
        raise ValueError("at least one control is required")
    instance_ids = tuple(control.instance_id for control in controls)
    if len(set(instance_ids)) != len(controls):
        raise ValueError("control instance ids must be unique")
    if any(not value.strip() for value in instance_ids):
        raise ValueError("control instance ids must not be blank")
    ordered = sorted(
        controls,
        key=lambda control: (
            control.anchor.list_id,
            control.anchor.paragraph,
            control.anchor.character,
            control.instance_id,
        ),
        reverse=True,
    )
    return tuple(DeleteControlCommand(control.instance_id) for control in ordered)
