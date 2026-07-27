"""공개 스키마 상한이 네이티브 실제 한계와 어긋나지 않는지 고정한다.

네이티브 숫자를 테스트에 베끼지 않고 C++ 원본에서 읽어 비교한다. 어느 한쪽이
움직이면 이 테스트가 깨지고, 어긋난 방향(막힘/거부)이 이름으로 드러난다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import ClassVar

import anyio
from mcp.types import Tool
import pytest
from pydantic import ValidationError


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY / "skills" / "automate-hancom-documents" / "scripts"
NATIVE = REPOSITORY / "addon" / "HancomLiveBridgeNative"
sys.path.insert(0, str(SCRIPTS))

from hwp_live_layout_contract import LayoutPlan  # noqa: E402
from hwp_live_native_layout import (  # noqa: E402
    NativeLayoutContext,
    build_native_layout_execution_plan,
    build_native_layout_request,
)
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_live_table_contract import (  # noqa: E402
    CellBorder,
    CellBorders,
    CellPadding,
    TableBlock,
    TableCell,
)
from hwp_mcp import build_server  # noqa: E402
from hwp_mcp_catalog import (  # noqa: E402
    host_visible_tool_catalog,
    proxy_tool_catalog,
    qa_tool_catalog,
    worker_tool_catalog,
)
from hwp_mcp_registry import McpProfile  # noqa: E402
from hwp_public_action_contract import PublicTextPatchTarget  # noqa: E402


class _NativeConstantMissing(LookupError):
    pass


def _native_constant(source: str, name: str) -> int:
    """C++ 원본에서 `constexpr ... name = 12'345;` 값을 읽는다."""
    text = (NATIVE / source).read_text(encoding="utf-8")
    match = re.search(
        rf"constexpr\s+[\w:]+(?:\s+[\w:]+)*\s+{re.escape(name)}\s*=\s*([0-9']+)\s*;",
        text,
    )
    if match is None:
        raise _NativeConstantMissing(f"{name} not found in {source}")
    return int(match.group(1).replace("'", ""))


def _reference_layout_shape_bound() -> int:
    """ReferenceLayoutValidation.cpp 의 행·열 상한을 읽는다."""
    text = (NATIVE / "ReferenceLayoutValidation.cpp").read_text(encoding="utf-8")
    rows = re.search(r"spec\.rows\s*<\s*1\s*\|\|\s*spec\.rows\s*>\s*(\d+)", text)
    columns = re.search(
        r"spec\.columns\s*<\s*1\s*\|\|\s*spec\.columns\s*>\s*(\d+)", text
    )
    if rows is None or columns is None:
        raise _NativeConstantMissing("reference layout shape bound not found")
    if rows.group(1) != columns.group(1):
        raise _NativeConstantMissing("reference layout rows and columns bounds differ")
    return int(rows.group(1))


def _fresh_tools(profile: McpProfile) -> tuple[Tool, ...]:
    server = build_server(LiveHwpController(), profile=profile)

    async def listed() -> tuple[Tool, ...]:
        return tuple(await server.list_tools())

    return anyio.run(listed)


class _Shapes:
    """테스트가 재사용하는 표 모양."""

    border: ClassVar[CellBorder] = CellBorder(
        style="solid", width="0.12mm", color=(0, 0, 0)
    )

    @classmethod
    def maximal_cell(cls) -> TableCell:
        borders = CellBorders(
            left=cls.border, right=cls.border, top=cls.border, bottom=cls.border
        )
        return TableCell(
            text="x",
            style_id=3,
            bold=True,
            font_name="함초롬바탕",
            font_size_pt=10,
            text_color=(0, 0, 0),
            alignment="center",
            vertical_alignment="center",
            line_spacing_percent=160,
            fill_color=(255, 255, 255),
            padding=CellPadding(),
            borders=borders,
        )

    @classmethod
    def table(cls, rows: int, columns: int, *, maximal: bool) -> TableBlock:
        cell = cls.maximal_cell() if maximal else TableCell()
        return TableBlock(
            kind="table",
            rows=tuple(tuple(cell for _ in range(columns)) for _ in range(rows)),
            column_widths_mm=(
                tuple(169.0 / columns for _ in range(columns)) if maximal else None
            ),
            row_heights_mm=tuple(8.0 for _ in range(rows)) if maximal else None,
            base_style_id=0,
        )


# --- 부재 증명 방지: 상수 판독기가 아는 정답을 실제로 찾아내는지 먼저 확인한다 -------


def test_native_constant_reader_finds_known_values() -> None:
    assert _native_constant("ActionProtocol.cpp", "kMaximumCommands") == 20_000
    assert _native_constant("ActionProtocol.cpp", "kMaximumPayloadCharacters") == (
        8_000_000
    )
    assert _native_constant("ActionTextPatch.cpp", "kMaximumSearches") == 20_000
    assert _native_constant("ActionTextPatch.cpp", "kMaximumCellTextPatches") == 100
    assert _reference_layout_shape_bound() == 50


def test_native_constant_reader_fails_loudly_when_absent() -> None:
    with pytest.raises(_NativeConstantMissing):
        _ = _native_constant("ActionProtocol.cpp", "kThisConstantDoesNotExist")


# --- ② occurrence: 스키마가 네이티브보다 높아 조용히 거부되던 방향 ----------------


def test_text_patch_occurrence_ceiling_matches_native_search_limit() -> None:
    ceiling = _native_constant("ActionTextPatch.cpp", "kMaximumSearches")
    accepted = PublicTextPatchTarget(kind="find", occurrence=ceiling)
    assert accepted.occurrence == ceiling


def test_text_patch_occurrence_beyond_native_search_limit_is_rejected() -> None:
    """안전. 네이티브가 절대 도달하지 못하는 요청은 스키마에서 막혀야 한다."""
    ceiling = _native_constant("ActionTextPatch.cpp", "kMaximumSearches")
    with pytest.raises(ValidationError):
        _ = PublicTextPatchTarget(kind="find", occurrence=ceiling + 1)


def test_text_patch_occurrence_still_accepts_the_common_first_match() -> None:
    assert PublicTextPatchTarget(kind="find", occurrence=1).occurrence == 1


# --- ③ 50x50 TableBlock 제출 가능 -----------------------------------------------


def test_table_block_shape_matches_native_reference_layout_bound() -> None:
    bound = _reference_layout_shape_bound()
    block = _Shapes.table(bound, bound, maximal=False)
    assert len(block.rows) == bound
    assert len(block.rows[0]) == bound


def test_table_block_rejects_shapes_beyond_the_native_bound() -> None:
    """안전. 네이티브 참조 레이아웃 상한을 넘는 모양은 계속 거부한다."""
    bound = _reference_layout_shape_bound()
    with pytest.raises(ValidationError):
        _ = _Shapes.table(bound + 1, 1, maximal=False)
    with pytest.raises(ValidationError):
        _ = _Shapes.table(1, bound + 1, maximal=False)


def test_layout_plan_accepts_a_maximal_table_block() -> None:
    bound = _reference_layout_shape_bound()
    plan = LayoutPlan(
        target="document_end",
        blocks=(_Shapes.table(bound, bound, maximal=False),),
    )
    cells = sum(len(row) for block in plan.blocks for row in block.rows)  # type: ignore[union-attr]
    assert cells == bound * bound


def test_layout_plan_rejects_more_cells_than_one_maximal_table() -> None:
    """안전. 예산은 최대 표 하나까지다. 그 위는 계속 거부한다."""
    bound = _reference_layout_shape_bound()
    with pytest.raises(ValidationError):
        _ = LayoutPlan(
            target="document_end",
            blocks=(
                _Shapes.table(bound, bound, maximal=False),
                _Shapes.table(1, 1, maximal=False),
            ),
        )


# --- 안전: 최악 규모가 네이티브 명령 한계를 넘지 않는지 --------------------------


def test_maximal_layout_never_sends_a_batch_over_the_native_command_limit() -> None:
    """안전. 새 예산의 최악 표가 kMaximumCommands 를 넘는 호출을 만들지 않는다."""
    limit = _native_constant("ActionProtocol.cpp", "kMaximumCommands")
    bound = _reference_layout_shape_bound()
    plan = LayoutPlan(
        target="document_end",
        blocks=(_Shapes.table(bound, bound, maximal=True),),
    )
    request = build_native_layout_request(
        NativeLayoutContext(document_id=1, full_name="C:/x.hwp", style_ids=()),
        plan,
        {},
    )
    assert len(request.commands) > limit, (
        "최악 표는 한 번에 보낼 수 없을 만큼 커야 분할 경로가 검증된다"
    )
    execution = build_native_layout_execution_plan(request)
    assert execution.batches
    assert all(len(batch.request.commands) <= limit for batch in execution.batches), (
        "분할된 호출 중 네이티브 명령 한계를 넘는 것이 있다"
    )


# --- 안전: 도구 수 불변 ---------------------------------------------------------


def test_public_tool_counts_are_unchanged_by_limit_alignment() -> None:
    """안전. 상한 값만 바꾼다. 도구 수 44/1/45/62 는 불변이다."""
    worker = worker_tool_catalog(_fresh_tools("production"))
    proxy = proxy_tool_catalog()
    host_visible = host_visible_tool_catalog(worker.tools)
    qa = qa_tool_catalog(_fresh_tools("qa"))
    assert (worker.count, proxy.count, host_visible.count, qa.count) == (44, 1, 45, 62)


def test_patch_text_target_field_structure_is_unchanged() -> None:
    """안전. 숫자 상한만 바꾼다. 필드 이름·타입·구조는 그대로다."""
    schema = PublicTextPatchTarget.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "kind",
        "start",
        "end",
        "occurrence",
        "match_case",
        "table_instance_id",
        "cell",
    }
