from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    CellCommand,
    NativeActionRequest,
    ParameterActionCommand,
)
from hwp_live_native_layout import (  # noqa: E402
    LAYOUT_COMMAND_LIMIT,
    LAYOUT_TOPOLOGY_WORK_BUDGET,
    build_native_layout_execution_plan,
)


# ActionProtocol.cpp:21 sets kMaximumCommands = 20'000 and ActionProtocol.cpp:537
# rejects the whole script with BAD_REQUEST past it, regardless of `atomic`.
# Anything over that limit that Python dispatches as a single request cannot
# succeed, so it has to fail here with a message that names the real cause.
NATIVE_HARD_COMMAND_LIMIT = 20_000


def _table_create() -> ParameterActionCommand:
    import test_hwp_live_layout_batching as batching

    return next(
        command
        for command in batching._request(
            batching._plan(batching._table(1, 1))
        ).commands
        if isinstance(command, ParameterActionCommand)
        and command.action == "TableCreate"
    )


def _request(count: int, *, atomic: bool) -> NativeActionRequest:
    return NativeActionRequest(
        17,
        "C:/documents/layout.hwp",
        (_table_create(), *(CellCommand("A1") for _ in range(count - 1))),
        None,
        None,
        atomic,
    )


def test_native_hard_command_limit_matches_action_protocol() -> None:
    assert LAYOUT_COMMAND_LIMIT == NATIVE_HARD_COMMAND_LIMIT


def test_atomic_request_over_native_command_limit_fails_before_dispatch() -> None:
    request = _request(LAYOUT_COMMAND_LIMIT + 1, atomic=True)

    with pytest.raises(HwpLiveError) as raised:
        _ = build_native_layout_execution_plan(request)

    assert "20000" in str(raised.value) or "20,000" in str(raised.value)


# --- safety: the fix must not weaken atomicity or the existing splitter -------


def test_atomic_request_within_command_limit_is_never_split() -> None:
    import test_hwp_live_layout_batching as batching
    from hwp_live_contract import LayoutPlan
    from hwp_live_native_layout import (
        _layout_command_groups,
        _layout_topology_work,
    )

    request = batching._request(
        LayoutPlan(
            target="document_end",
            blocks=(batching._table(20, 20, padding="checkerboard"),),
        ),
        atomic=True,
    )
    work = _layout_topology_work(_layout_command_groups(request))

    # Precondition: this request is over the Python topology ceiling but under
    # the native command cap. Atomic requests are allowed to spend that budget
    # because splitting them would leave a partially applied layout behind.
    assert work > LAYOUT_TOPOLOGY_WORK_BUDGET
    assert len(request.commands) <= LAYOUT_COMMAND_LIMIT

    execution = build_native_layout_execution_plan(request)

    assert len(execution.batches) == 1
    assert execution.batches[0].request is request
    assert execution.batches[0].request.atomic is True


def test_non_atomic_request_over_command_limit_still_splits() -> None:
    request = _request(LAYOUT_COMMAND_LIMIT + 1, atomic=False)

    execution = build_native_layout_execution_plan(request)

    assert len(execution.batches) == 2
    assert all(
        len(batch.request.commands) <= LAYOUT_COMMAND_LIMIT
        for batch in execution.batches
    )
