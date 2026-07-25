from __future__ import annotations

# noqa: E501  # noqa: SIZE_OK — this file is the exhaustive public capability contract matrix.

import sys
from base64 import b64encode
from pathlib import Path
from typing import ClassVar

import anyio
import pytest
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_live_contract import ImageBlock, LayoutPlan  # noqa: E402
from hwp_live_native_action_models import (  # noqa: E402
    BooleanValue,
    CellCommand,
    IntegerValue,
    MergeCommand,
    MillimeterValue,
    ParameterActionCommand,
    RunCommand,
)
from hwp_live_native_action_contract import decode_page_inspection  # noqa: E402
from hwp_live_native_format_commands import (  # noqa: E402
    MergeCommandPlan,
    SplitCommandPlan,
    TableFormatCommandPlan,
    build_native_format_commands,
)
from hwp_live_native_format_inputs import (  # noqa: E402
    InputFailure,
    MergeSpec,
    SplitSpec,
    parse_table_format,
    parse_text_format,
)
from hwp_live_native_format_target import ResolvedTable  # noqa: E402
from hwp_live_native_table_layout import table_commands  # noqa: E402
from hwp_live_session import LiveHwpController  # noqa: E402
from hwp_live_structure_contract import (  # noqa: E402
    FastPageCell,
    FastPageControl,
    FastPageInspection,
    StructurePosition,
)
from hwp_live_table_contract import CellPadding, TableBlock, TableCell  # noqa: E402
from hwp_mcp import build_server  # noqa: E402
from hwp_mcp_registry import tool_names  # noqa: E402
from hwp_operation_contract import (  # noqa: E402
    HwpOperateGuards,
    HwpOperateInputs,
    OperationResult,
)
from hwp_priority_recipe_contract import HwpPriorityRecipeInputs  # noqa: E402
from hwp_public_action_contract import (  # noqa: E402
    OBJECT_INPUT_ALIASES,
    PublicImageSize,
)
from hwp_public_contract import (  # noqa: E402
    PublicTableTarget,
    to_public_action_result,
)
from hwp_public_table_edit_contract import (  # noqa: E402
    PublicSplitTableCellInput,
    PublicTableFormattingInput,
)
from hwp_public_table_target import PublicTableTargetStore  # noqa: E402
from hwp_public_tools import HwpPublicTools  # noqa: E402
from hwp_report_layout import ReportFigure  # noqa: E402


class _ToolSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    required: tuple[str, ...] = ()
    properties: dict[str, JsonValue] = Field(default_factory=dict)


class _CompatibilityManifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    production_sessionless_reads: tuple[str, ...]
    production_tools: tuple[str, ...]
    minimum_explicit_dimension_mm: float
    minimum_font_size_pt: float
    document_analysis_render_api: str


class _AmbiguousExecutor:
    async def execute(
        self,
        intent: str,
        inputs: HwpOperateInputs,
        guards: HwpOperateGuards | None,
    ) -> OperationResult:
        _ = inputs, guards
        return OperationResult(
            status="ambiguous",
            query=intent,
            registry_entries=1,
            lookup_microseconds=0,
            message="choose a target",
        )


def _table_inspection() -> FastPageInspection:
    return FastPageInspection(
        document_id=17,
        full_name="C:/documents/sample.hwp",
        page=2,
        page_count=3,
        text="",
        controls=(
            FastPageControl(
                control_type="tbl",
                instance_id="native-table-42",
                anchor=StructurePosition(list_id=7, paragraph=3, character=0),
                rows=1,
                columns=1,
            ),
        ),
    )


def test_explicit_one_millimeter_dimensions_are_preserved() -> None:
    # Given
    image_path = Path("C:/fixtures/one-mm.png")

    # When
    public_size = PublicImageSize(width_mm=1.0, height_mm=1.0)
    recipe = HwpPriorityRecipeInputs(
        picture_width_mm=1.0,
        picture_height_mm=1.0,
    )
    image = ImageBlock(
        kind="image",
        path=image_path,
        width_mm=1.0,
        height_mm=1.0,
    )
    cell = TableCell(
        image_path=image_path,
        image_width_mm=1.0,
        image_height_mm=1.0,
    )
    tiny_cell = TableCell(
        font_size_pt=1.0,
        line_spacing_percent=50,
        padding=CellPadding(left_mm=0, right_mm=0, top_mm=0, bottom_mm=0),
    )
    table = TableBlock(
        kind="table",
        base_style_id=0,
        rows=((tiny_cell,),),
        column_widths_mm=(1.0,),
        minimum_column_widths_mm=(1.0,),
        row_heights_mm=(1.0,),
    )
    plan = LayoutPlan(blocks=(image, table))
    figure = ReportFigure(path=image_path, width_mm=1.0, height_mm=1.0)
    public_table_format = PublicTableFormattingInput(cell="A1", font_size_pt=1.0)
    native_text_format = parse_text_format({"font_size_pt": 1.0})

    # Then
    assert public_size.width_mm == 1.0
    assert recipe.picture_height_mm == 1.0
    assert cell.image_width_mm == 1.0
    assert plan.blocks == (image, table)
    assert table.column_widths_mm == (1.0,)
    assert table.row_heights_mm == (1.0,)
    assert tiny_cell.font_size_pt == 1.0
    assert public_table_format.font_size_pt == 1.0
    assert not isinstance(native_text_format, InputFailure)
    assert figure.width_mm == 1.0


def test_explicit_table_geometry_is_reapplied_after_cell_formatting() -> None:
    cell = TableCell(
        font_size_pt=1.0,
        line_spacing_percent=50,
        padding=CellPadding(left_mm=0, right_mm=0, top_mm=0, bottom_mm=0),
    )
    table = TableBlock(
        kind="table",
        base_style_id=0,
        rows=((cell, cell), (cell, cell)),
        column_widths_mm=(1.0, 1.0),
        minimum_column_widths_mm=(1.0, 1.0),
        row_heights_mm=(1.0, 1.0),
    )

    commands = table_commands(table, {}, {}, {}, {})
    geometry_indices = [
        index
        for index, command in enumerate(commands)
        if isinstance(command, ParameterActionCommand)
        and any(
            setter.path in {"ShapeTableCell/Width", "ShapeTableCell/Height"}
            for setter in command.setters
        )
    ]
    character_indices = [
        index
        for index, command in enumerate(commands)
        if isinstance(command, ParameterActionCommand) and command.action == "CharShape"
    ]

    assert len(geometry_indices) == 8
    assert max(geometry_indices) > max(character_indices)


def test_existing_table_cell_can_resize_its_entire_row_and_column() -> None:
    requested = PublicTableFormattingInput(
        cell="B1",
        row_height_mm=10.0,
        column_width_mm=20.0,
    )

    parsed = parse_table_format(requested.to_parameters())

    assert not isinstance(parsed, InputFailure)
    assert parsed.row_height_mm == 10.0
    assert parsed.column_width_mm == 20.0

    commands = build_native_format_commands(
        TableFormatCommandPlan(
            parsed,
            ResolvedTable(
                instance_id="table-4x4",
                basis="target.control_instance_id",
                page=1,
                rows=4,
                columns=4,
            ),
        )
    )
    selected_cells = [
        command.address for command in commands if isinstance(command, CellCommand)
    ]
    navigation = [
        command.action for command in commands if isinstance(command, RunCommand)
    ]
    sizes = {
        setter.path: setter.value.value
        for command in commands
        if isinstance(command, ParameterActionCommand)
        for setter in command.setters
        if isinstance(setter.value, MillimeterValue)
    }
    size_unlocks = [
        index
        for index, command in enumerate(commands)
        if isinstance(command, ParameterActionCommand)
        and command.action == "TablePropertyDialog"
        and any(
            setter.path == "ProtectSize"
            and isinstance(setter.value, BooleanValue)
            and setter.value.value is False
            for setter in command.setters
        )
    ]

    assert selected_cells == ["B1", "B1"]
    assert navigation.count("TableCellBlockCol") == 1
    assert navigation.count("TableCellBlockRow") == 1
    assert "TableLowerCell" not in navigation
    assert "TableRightCell" not in navigation
    assert size_unlocks == [2]
    assert sizes == {
        "ShapeTableCell/Width": 20.0,
        "ShapeTableCell/Height": 10.0,
    }


def test_existing_table_merge_and_split_clear_size_protection_first() -> None:
    table = ResolvedTable(
        instance_id="protected-table",
        basis="target.control_instance_id",
        page=5,
        rows=2,
        columns=3,
    )
    merge_commands = build_native_format_commands(
        MergeCommandPlan(MergeSpec("A2", "B2"), table)
    )
    split_commands = build_native_format_commands(
        SplitCommandPlan(SplitSpec("C2", 2, 1, False, False, "equal"), table)
    )

    for commands in (merge_commands, split_commands):
        unlock_index = next(
            index
            for index, command in enumerate(commands)
            if isinstance(command, ParameterActionCommand)
            and command.action == "TablePropertyDialog"
            and any(
                setter.path == "ProtectSize"
                and isinstance(setter.value, BooleanValue)
                and setter.value.value is False
                for setter in command.setters
            )
        )
        mutation_index = next(
            index
            for index, command in enumerate(commands)
            if isinstance(command, MergeCommand)
            or (
                isinstance(command, ParameterActionCommand)
                and command.action == "TableSplitCell"
            )
        )
        assert unlock_index < mutation_index


def test_public_single_cell_split_does_not_enable_grid_adjustment_mode() -> None:
    requested = PublicSplitTableCellInput(cell="C2", columns=2, rows=1)

    parameters = requested.to_parameters()
    split = SplitSpec(
        cell="C2",
        columns=2,
        rows=1,
        distribute_height=False,
        merge=False,
        split_mode="equal",
    )
    commands = build_native_format_commands(
        SplitCommandPlan(
            split,
            ResolvedTable(
                instance_id="table-2x3",
                basis="target.control_instance_id",
                page=5,
                rows=2,
                columns=3,
            ),
        )
    )
    mode2 = next(
        setter.value
        for command in commands
        if isinstance(command, ParameterActionCommand)
        and command.action == "TableSplitCell"
        for setter in command.setters
        if setter.path == "Mode2"
    )

    assert parameters["split_mode"] == "equal"
    assert isinstance(mode2, IntegerValue)
    assert mode2.value == 0


def test_native_cell_formatting_requires_post_action_property_readback() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "addon"
        / "HancomLiveBridgeNative"
        / "ActionExecutor.cpp"
    ).read_text(encoding="utf-8")

    assert "VerifyAppliedCellFormat" in source
    assert 'command.name == L"CellFill"' in source
    assert 'command.name == L"CellBorder"' in source


def test_native_cell_format_readback_reenters_the_last_target_cell() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "addon"
        / "HancomLiveBridgeNative"
        / "ActionExecutor.cpp"
    ).read_text(encoding="utf-8")

    assert "std::wstring currentCell;" in source
    assert "context->currentCell = address;" in source
    assert "!GoToCell(context, context->currentCell)" in source


def test_native_table_scans_include_locally_split_cells_beyond_grid_end() -> None:
    root = Path(__file__).resolve().parents[1] / "addon" / "HancomLiveBridgeNative"
    action_source = (root / "ActionExecutor.cpp").read_text(encoding="utf-8")
    inspection_source = (root / "TableInspection.cpp").read_text(encoding="utf-8")

    assert "InspectTableTopology" in action_source
    assert "ExtendTableListEnd" in inspection_source
    assert "lastList = ExtendTableListEnd" in inspection_source


def test_format_table_schema_exposes_existing_row_and_column_dimensions() -> None:
    server = build_server(LiveHwpController(), profile="production")

    tools = anyio.run(server.list_tools)
    schemas = {
        tool.name: _ToolSchema.model_validate(tool.inputSchema) for tool in tools
    }

    properties = schemas["hwp_format_table"].properties
    assert "row_height_mm" in properties
    assert "column_width_mm" in properties
    assert "cell" not in schemas["hwp_format_table"].required


def test_fast_inspection_reports_actual_cell_width_and_height() -> None:
    def encoded(value: str) -> str:
        return b64encode(value.encode("utf-8")).decode("ascii")

    payload = "\n".join(
        (
            "HPI1",
            f"DOC\t17\t{encoded('C:/documents/sample.hwp')}",
            f"PAGE\t1\t1\t{encoded('')}",
            f"CTRL\t{encoded('tbl')}\t{encoded('table-1')}\t0\t1\t0\t1\t1\t5670\t2835",
            f"CELL\t{encoded('table-1')}\t{encoded('A1')}\t7\t1\t1\t{encoded('')}\t5670\t2835",
            "END",
        )
    )

    decoded = decode_page_inspection(payload)
    cell = decoded.cells[0]
    public = FastPageCell(
        table_instance_id=cell.table_instance_id,
        address=cell.address,
        list_id=cell.list_id,
        row_span=cell.row_span,
        column_span=cell.column_span,
        text=cell.text,
        width_hwpunit=cell.width_hwpunit,
        height_hwpunit=cell.height_hwpunit,
        width_mm=20.0,
        height_mm=10.0,
    )

    assert cell.width_hwpunit == 5670
    assert cell.height_hwpunit == 2835
    assert public.width_mm == 20.0
    assert public.height_mm == 10.0


def test_fast_inspection_table_id_resolves_in_nested_table_target() -> None:
    # Given
    store = PublicTableTargetStore()
    inspection = _table_inspection()

    # When
    store.remember_inspection(inspection)
    resolved = store.resolve(PublicTableTarget(target_id="native-table-42"))

    # Then
    assert resolved.document_path == inspection.full_name
    assert resolved.target.page_hint == inspection.page
    assert resolved.target.table_index == 1
    assert resolved.target.control_instance_id == "native-table-42"


def test_production_render_page_is_sessionless_and_public() -> None:
    # Given
    server = build_server(LiveHwpController(), profile="production")

    # When
    tools = anyio.run(server.list_tools)
    schemas = {
        tool.name: _ToolSchema.model_validate(tool.inputSchema) for tool in tools
    }

    # Then
    render = schemas["hwp_render_page"]
    assert "session_id" not in render.required
    assert "document_path" in render.properties


def test_target_retry_field_matches_each_public_tool_shape() -> None:
    # Given
    ambiguous = OperationResult(
        status="ambiguous",
        query="select target",
        registry_entries=1,
        lookup_microseconds=0,
        message="choose a target",
    )

    # When
    object_result = to_public_action_result(
        ambiguous,
        (),
        OBJECT_INPUT_ALIASES,
    )
    table_result = to_public_action_result(ambiguous, ())

    # Then
    assert object_result.required_inputs == ("target_id",)
    assert table_result.required_inputs == ("target.target_id",)


def test_fill_table_reports_its_direct_target_id_field() -> None:
    # Given
    tools = HwpPublicTools(_AmbiguousExecutor())

    async def fill() -> tuple[str, ...]:
        result = await tools.hwp_fill_table(
            operation_id="fill-direct-target",
            records=[{"Name": "value"}],
        )
        return result.required_inputs

    # When
    required_inputs = anyio.run(fill)

    # Then
    assert required_inputs == ("target_id",)


def test_tool_schemas_keep_direct_and_nested_target_forms_distinct() -> None:
    # Given
    server = build_server(LiveHwpController(), profile="production")

    # When
    tools = anyio.run(server.list_tools)
    schemas = {
        tool.name: _ToolSchema.model_validate(tool.inputSchema) for tool in tools
    }

    # Then
    assert "target_id" in schemas["hwp_add_caption"].properties
    assert "target" not in schemas["hwp_add_caption"].properties
    assert "target_id" in schemas["hwp_fill_table"].properties
    assert "target" not in schemas["hwp_fill_table"].properties
    for name in (
        "hwp_expand_and_fill_table",
        "hwp_format_table",
        "hwp_merge_table_cells",
        "hwp_split_table_cell",
    ):
        assert "target" in schemas[name].properties
        assert "target_id" not in schemas[name].properties


def test_compatibility_manifest_matches_production_registry() -> None:
    # Given
    manifest_path = Path(__file__).resolve().parents[1] / "compatibility-manifest.json"

    # When
    manifest = _CompatibilityManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )

    # Then
    assert frozenset(manifest.production_tools) == tool_names("production")
    assert "hwp_render_page" in manifest.production_sessionless_reads
    assert manifest.minimum_explicit_dimension_mm == 1.0
    assert manifest.minimum_font_size_pt == 1.0
    assert manifest.document_analysis_render_api == "IHwpObject.CreatePageImage"


def test_operation_results_reject_stale_native_protocol_eight() -> None:
    fields = {
        "status": "executed",
        "query": "native protocol",
        "registry_entries": 1,
        "lookup_microseconds": 0,
        "message": "completed",
    }

    with pytest.raises(ValidationError):
        _ = OperationResult.model_validate({**fields, "native_protocol": 8})

    current = OperationResult.model_validate({**fields, "native_protocol": 9})
    assert current.native_protocol == 9
