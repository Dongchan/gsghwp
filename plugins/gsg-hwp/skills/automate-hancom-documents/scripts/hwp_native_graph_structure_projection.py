from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from _hwp_native_graph_errors import GraphProtocolError
from _hwp_native_graph_wire import decode_nested_fields
from hwp_live_native_action_models import (
    NativeDetailedCell,
    NativeDetailedControl,
    NativeDetailedInspection,
    NativePosition,
)
from hwp_live_native_action_results import (
    NativeCellBorder,
    NativeCellFormat,
    NativeDetailedCaption,
    NativeDetailedParagraph,
)
from hwp_native_graph_cache import NativeGraphSnapshot
from hwp_native_graph_models import (
    NativeBlobSlice,
    NativeBoolean,
    NativeEdgeRecord,
    NativeGraphPresent,
    NativeIdentifier,
    NativeInteger,
    NativeNodeRecord,
    NativeOpaque,
    NativePropertyRecord,
    NativeRawUtf16,
    NativeScalarValue,
    NodeKind,
    RecordKind,
    ScalarTag,
    TypedNativeRecord,
)


@dataclass(frozen=True, slots=True)
class _Node:
    record: NativeNodeRecord
    parent: bytes | None
    payload: dict[int, object]


@dataclass(frozen=True, slots=True)
class _CellAppearance:
    fill_color: int | None
    fill_brush: int | None
    border_left: NativeCellBorder
    border_right: NativeCellBorder
    border_top: NativeCellBorder
    border_bottom: NativeCellBorder
    margin_left: int | None
    margin_right: int | None
    margin_top: int | None
    margin_bottom: int | None
    vertical_align: int | None
    alignment: int | None
    face_name: str | None
    character_height: int | None
    bold: bool | None


def _nested(value: object) -> dict[int, object]:
    if not isinstance(value, NativeOpaque) or value.scalar is not ScalarTag.STRUCT:
        raise GraphProtocolError("HDS1_GRAPH_STRUCT")
    return {field.tag: field.value for field in decode_nested_fields(value)}


def _present(value: object) -> NativeScalarValue | None:
    return value.value if isinstance(value, NativeGraphPresent) else None


def _integer(value: object) -> int | None:
    scalar = _present(value)
    if scalar is None:
        scalar = value if isinstance(value, NativeInteger) else None
    return scalar.value if isinstance(scalar, NativeInteger) else None


def _boolean(value: object) -> bool | None:
    scalar = _present(value)
    return scalar.value if isinstance(scalar, NativeBoolean) else None


def _utf16(value: object) -> str | None:
    scalar = _present(value)
    if not isinstance(scalar, NativeRawUtf16):
        return None
    raw = scalar.raw
    if not isinstance(raw, bytes):
        raw = b"".join(raw.chunks())
    return raw.decode("utf-16-le", errors="surrogatepass")


def _position(value: object) -> NativePosition | None:
    scalar = _present(value)
    if not isinstance(scalar, NativeOpaque) or scalar.scalar is not ScalarTag.STRUCT:
        return None
    raw = scalar.raw
    if not isinstance(raw, bytes) or len(raw) != 24:
        return None
    return NativePosition(
        list_id=int.from_bytes(raw[0:8], "little", signed=True),
        paragraph=int.from_bytes(raw[8:16], "little", signed=True),
        character=int.from_bytes(raw[16:24], "little", signed=True),
    )


def _blob(value: object) -> NativeBlobSlice | None:
    scalar = _present(value)
    return scalar if isinstance(scalar, NativeBlobSlice) else None


def _blob_text(snapshot: NativeGraphSnapshot, blob: NativeBlobSlice) -> str:
    raw = snapshot.read_blob(blob)
    if len(raw) < 8:
        raise GraphProtocolError("HDS1_GRAPH_TEXT")
    units = int.from_bytes(raw[:8], "little")
    if units > (len(raw) - 8) // 2 or len(raw) != 8 + units * 2:
        raise GraphProtocolError("HDS1_GRAPH_TEXT")
    return raw[8:].decode("utf-16-le", errors="surrogatepass")


def _node(record: NativeNodeRecord) -> _Node:
    outer = {field.tag: field.value for field in record.fields}
    common = _nested(outer.get(1))
    payload = _nested(outer.get(2))
    parent_value = common.get(3)
    parent = parent_value.value if isinstance(parent_value, NativeIdentifier) else None
    return _Node(record=record, parent=parent, payload=payload)


def _projection_records(
    snapshot: NativeGraphSnapshot,
    records: tuple[TypedNativeRecord, ...] | None,
) -> tuple[TypedNativeRecord, ...]:
    if records is not None:
        return records
    kinds = (RecordKind.NODE, RecordKind.EDGE, RecordKind.PROPERTY)
    _empty, total = snapshot.indexed_records(kinds, 0, 0)
    if total == 0:
        return ()
    selected, _ = snapshot.indexed_records(kinds, 0, total)
    return tuple(selected)


def native_detailed_inspection_from_graph(
    snapshot: NativeGraphSnapshot,
    records: tuple[TypedNativeRecord, ...] | None = None,
    *,
    document_id: int,
    full_name: str,
    page: int,
    page_count: int,
) -> NativeDetailedInspection:
    """Project authenticated typed HGN1 records into the frozen HDS1 input model."""
    records = _projection_records(snapshot, records)
    nodes = {
        record.node_id: _node(record)
        for record in records
        if isinstance(record, NativeNodeRecord)
    }
    children: defaultdict[bytes, list[_Node]] = defaultdict(list)
    for node in nodes.values():
        if node.parent is not None:
            children[node.parent].append(node)
    properties: defaultdict[bytes, dict[int, object]] = defaultdict(dict)
    style_targets: dict[bytes, bytes] = {}
    owner_targets: dict[bytes, bytes] = {}
    for record in records:
        if isinstance(record, NativePropertyRecord):
            properties[record.owner_node_id][record.property_key] = record.observation
        elif isinstance(record, NativeEdgeRecord):
            fields = {field.tag: field.value for field in record.fields}
            source = fields.get(2)
            target = fields.get(3)
            edge_kind = _integer(fields.get(1))
            if isinstance(source, NativeIdentifier) and isinstance(
                target, NativeIdentifier
            ):
                if edge_kind == 2:
                    style_targets[source.value] = target.value
                elif edge_kind == 9:
                    owner_targets[source.value] = target.value

    def paragraph_style(node: _Node) -> tuple[int | None, str | None]:
        target = style_targets.get(node.record.node_id)
        definition = nodes.get(target) if target is not None else None
        if definition is None or definition.record.node_kind is not NodeKind.DEFINITION:
            return None, None
        return (
            _integer(definition.payload.get(101)),
            _utf16(properties[definition.record.node_id].get(3000)),
        )

    def paragraph_style_id(node: _Node) -> int | None:
        return paragraph_style(node)[0]

    def span(node: _Node) -> tuple[int, int]:
        values = properties[node.record.node_id]
        return _integer(values.get(12000)) or 1, _integer(values.get(12001)) or 1

    def descendants(node: _Node, kind: NodeKind) -> list[_Node]:
        result: list[_Node] = []
        pending = list(children[node.record.node_id])
        while pending:
            current = pending.pop(0)
            if current.record.node_kind is kind:
                result.append(current)
            pending[0:0] = children[current.record.node_id]
        return result

    def text_for(node: _Node) -> str:
        parts: list[str] = []
        for run in descendants(node, NodeKind.CHARACTER_RUN):
            reference = _blob(run.payload.get(101))
            if reference is not None:
                parts.append(_blob_text(snapshot, reference))
        return "".join(parts)

    def has_ancestor(node: _Node, kind: NodeKind) -> bool:
        parent = node.parent
        while parent is not None and parent in nodes:
            ancestor = nodes[parent]
            if ancestor.record.node_kind is kind:
                return True
            parent = ancestor.parent
        return False

    def has_caption_story_ancestor(node: _Node) -> bool:
        parent = node.parent
        while parent is not None and parent in nodes:
            ancestor = nodes[parent]
            if ancestor.record.node_kind is NodeKind.STORY:
                return _integer(ancestor.payload.get(100)) == 5
            parent = ancestor.parent
        return False

    all_body_paragraphs: list[NativeDetailedParagraph] = []
    for node in nodes.values():
        if (
            node.record.node_kind is not NodeKind.PARAGRAPH
            or has_ancestor(node, NodeKind.TABLE_CELL)
            or has_caption_story_ancestor(node)
        ):
            continue
        first, last = span(node)
        position = _position(node.payload.get(100))
        if position is None:
            raise GraphProtocolError("HDS1_GRAPH_PARAGRAPH_POSITION")
        values = properties[node.record.node_id]
        runs = descendants(node, NodeKind.CHARACTER_RUN)
        references = [_blob(run.payload.get(101)) for run in runs]
        text_available = all(reference is not None for reference in references)
        text = text_for(node) if text_available else ""
        all_body_paragraphs.append(
            NativeDetailedParagraph(
                position=position,
                page_start=first,
                page_end=last,
                text_available=text_available,
                text=text,
                style_id=paragraph_style_id(node),
                face_name=_utf16(values.get(1000)),
                height_hwpunit=_integer(values.get(1007)),
                bold=_boolean(values.get(1008)),
                text_color=_integer(values.get(1010)),
                alignment=_integer(values.get(2000)),
                line_spacing=_integer(values.get(2002)),
                left_margin_hwpunit=_integer(values.get(2003)),
                right_margin_hwpunit=_integer(values.get(2004)),
                indentation_hwpunit=_integer(values.get(2005)),
                previous_spacing_hwpunit=_integer(values.get(2006)),
                next_spacing_hwpunit=_integer(values.get(2007)),
                heading_type=_integer(values.get(2012)),
                heading_level=_integer(values.get(2013)),
            )
        )
    all_body_paragraphs.sort(
        key=lambda item: (item.position.list_id, item.position.paragraph)
    )
    body = [item for item in all_body_paragraphs if item.position.list_id == 0]
    selected: list[NativeDetailedParagraph] = []
    previous: NativeDetailedParagraph | None = None
    for paragraph in body:
        if paragraph.page_end < page:
            previous = paragraph
            continue
        if previous is not None:
            selected.append(previous)
            previous = None
        selected.append(paragraph)
        if paragraph.page_start > page:
            break
    if not selected and previous is not None:
        selected.append(previous)

    paragraphs_complete = True
    paragraph_scan_error: str | None = None
    if len(selected) > 512:
        selected = selected[:512]
        paragraphs_complete = False
        paragraph_scan_error = "page paragraph record limit was reached"
    paragraphs: list[NativeDetailedParagraph] = []
    text_characters = 0
    for paragraph in selected:
        text = paragraph.text
        if len(text) > 16_000:
            text = text[:16_000]
            paragraphs_complete = False
            paragraph_scan_error = "page paragraph character limit was reached"
        remaining = max(0, 64_000 - text_characters)
        if len(text) > remaining:
            text = text[:remaining]
            paragraphs_complete = False
            paragraph_scan_error = "page paragraph text budget was reached"
        text_characters += len(text)
        paragraphs.append(
            NativeDetailedParagraph(
                position=paragraph.position,
                page_start=paragraph.page_start,
                page_end=paragraph.page_end,
                text_available=paragraph.text_available,
                text=text,
                style_id=paragraph.style_id,
                face_name=paragraph.face_name,
                height_hwpunit=paragraph.height_hwpunit,
                bold=paragraph.bold,
                text_color=paragraph.text_color,
                alignment=paragraph.alignment,
                line_spacing=paragraph.line_spacing,
                left_margin_hwpunit=paragraph.left_margin_hwpunit,
                right_margin_hwpunit=paragraph.right_margin_hwpunit,
                indentation_hwpunit=paragraph.indentation_hwpunit,
                previous_spacing_hwpunit=paragraph.previous_spacing_hwpunit,
                next_spacing_hwpunit=paragraph.next_spacing_hwpunit,
                heading_type=paragraph.heading_type,
                heading_level=paragraph.heading_level,
            )
        )

    controls: list[NativeDetailedControl] = []
    cells: list[NativeDetailedCell] = []
    control_kinds = {NodeKind.GENERIC_CONTROL, NodeKind.TABLE, NodeKind.IMAGE}
    for node in nodes.values():
        if node.record.node_kind not in control_kinds:
            continue
        first, last = span(node)
        if first > page or last < page:
            continue
        ctrl_id = _utf16(node.payload.get(100))
        instance = _utf16(node.payload.get(101))
        position = _position(node.payload.get(103))
        if ctrl_id is None or instance is None or position is None:
            continue
        values = properties[node.record.node_id]
        controls.append(
            NativeDetailedControl(
                control_type=ctrl_id,
                instance_id=instance,
                user_description=_utf16(node.payload.get(102)) or "",
                anchor=position,
                page_start=first,
                page_end=last,
                top_level=not has_ancestor(node, NodeKind.TABLE_CELL),
                rows=_integer(node.payload.get(107))
                if node.record.node_kind is NodeKind.TABLE
                else None,
                columns=_integer(node.payload.get(108))
                if node.record.node_kind is NodeKind.TABLE
                else None,
                width_hwpunit=_integer(values.get(12002)),
                height_hwpunit=_integer(values.get(12003)),
            )
        )

    for node in nodes.values():
        if node.record.node_kind is not NodeKind.TABLE_CELL:
            continue
        table_id = node.parent
        table = nodes.get(table_id) if table_id is not None else None
        if table is None:
            raise GraphProtocolError("HDS1_GRAPH_CELL_OWNER")
        instance = _utf16(table.payload.get(101))
        address = _utf16(node.payload.get(101))
        first, last = span(node)
        if instance is None or address is None or first > page or last < page:
            continue
        values = properties[node.record.node_id]
        cells.append(
            NativeDetailedCell(
                table_instance_id=instance,
                address=address,
                list_id=_integer(node.payload.get(106)) or 0,
                row_span=_integer(node.payload.get(104)) or 1,
                column_span=_integer(node.payload.get(105)) or 1,
                page_start=first,
                page_end=last,
                text=text_for(node),
                width_hwpunit=_integer(values.get(10000)),
                height_hwpunit=_integer(values.get(10001)),
            )
        )

    cell_formats: list[NativeCellFormat] = []
    for table in nodes.values():
        if table.record.node_kind is not NodeKind.TABLE:
            continue
        instance = _utf16(table.payload.get(101))
        rows = _integer(table.payload.get(107))
        columns = _integer(table.payload.get(108))
        if instance is None or rows is None or columns is None:
            continue
        physical = [
            child
            for child in children[table.record.node_id]
            if child.record.node_kind is NodeKind.TABLE_CELL
        ]
        middle_row = (rows + 1) // 2
        early_row = 2 if rows > 1 else 1
        middle_column = (columns + 1) // 2
        wanted = (
            (1, 1),
            (1, middle_column),
            (1, columns),
            (early_row, 1),
            (early_row, middle_column),
            (early_row, columns),
            (middle_row, 1),
            (middle_row, middle_column),
            (middle_row, columns),
            (rows, 1),
            (rows, middle_column),
            (rows, columns),
        )
        sampled: list[_Node] = []
        for row, column in wanted:
            owner = next(
                (
                    cell
                    for cell in physical
                    if (_integer(cell.payload.get(102)) or 0)
                    <= row
                    < (_integer(cell.payload.get(102)) or 0)
                    + (_integer(cell.payload.get(104)) or 0)
                    and (_integer(cell.payload.get(103)) or 0)
                    <= column
                    < (_integer(cell.payload.get(103)) or 0)
                    + (_integer(cell.payload.get(105)) or 0)
                ),
                None,
            )
            if owner is not None and owner not in sampled:
                sampled.append(owner)
            if len(sampled) == 12:
                break

        grouped: dict[_CellAppearance, list[str]] = {}
        for cell in sampled:
            values = properties[cell.record.node_id]
            address = _utf16(cell.payload.get(101))
            if address is None:
                continue
            appearance = _CellAppearance(
                fill_color=_integer(values.get(10008)),
                fill_brush=_integer(values.get(10009)),
                border_left=NativeCellBorder(
                    line_type=_integer(values.get(10010)),
                    width=_integer(values.get(10011)),
                    color=_integer(values.get(10012)),
                ),
                border_right=NativeCellBorder(
                    line_type=_integer(values.get(10013)),
                    width=_integer(values.get(10014)),
                    color=_integer(values.get(10015)),
                ),
                border_top=NativeCellBorder(
                    line_type=_integer(values.get(10016)),
                    width=_integer(values.get(10017)),
                    color=_integer(values.get(10018)),
                ),
                border_bottom=NativeCellBorder(
                    line_type=_integer(values.get(10019)),
                    width=_integer(values.get(10020)),
                    color=_integer(values.get(10021)),
                ),
                margin_left=_integer(values.get(10002)),
                margin_right=_integer(values.get(10003)),
                margin_top=_integer(values.get(10004)),
                margin_bottom=_integer(values.get(10005)),
                vertical_align=_integer(values.get(10006)),
                alignment=_integer(values.get(10022)),
                face_name=_utf16(values.get(10023)),
                character_height=_integer(values.get(10024)),
                bold=_boolean(values.get(10025)),
            )
            grouped.setdefault(appearance, []).append(address)
        for appearance, addresses in grouped.items():
            cell_formats.append(
                NativeCellFormat(
                    table_instance_id=instance,
                    addresses=tuple(addresses),
                    fill_color=appearance.fill_color,
                    fill_brush=appearance.fill_brush,
                    border_left=appearance.border_left,
                    border_right=appearance.border_right,
                    border_top=appearance.border_top,
                    border_bottom=appearance.border_bottom,
                    margin_left_hwpunit=appearance.margin_left,
                    margin_right_hwpunit=appearance.margin_right,
                    margin_top_hwpunit=appearance.margin_top,
                    margin_bottom_hwpunit=appearance.margin_bottom,
                    vertical_align=appearance.vertical_align,
                    alignment=appearance.alignment,
                    face_name=appearance.face_name,
                    character_height=appearance.character_height,
                    bold=appearance.bold,
                )
            )

    captions: list[NativeDetailedCaption] = []
    for node in nodes.values():
        if (
            node.record.node_kind is not NodeKind.STORY
            or _integer(node.payload.get(100)) != 5
        ):
            continue
        owner = owner_targets.get(node.record.node_id)
        table = nodes.get(owner) if owner is not None else None
        if table is None or table.record.node_kind is not NodeKind.TABLE:
            continue
        instance = _utf16(table.payload.get(101))
        paragraphs_in_caption = descendants(node, NodeKind.PARAGRAPH)
        if instance is None or not paragraphs_in_caption:
            continue
        spans = [span(paragraph) for paragraph in paragraphs_in_caption]
        first = min(item[0] for item in spans)
        last = max(item[1] for item in spans)
        if first > page or last < page:
            continue
        style_id, style_name = paragraph_style(paragraphs_in_caption[0])
        if style_name is None:
            style_name = _utf16(
                properties[paragraphs_in_caption[0].record.node_id].get(1000)
            )
        if style_name is None:
            style_name = next(
                (
                    name
                    for run in descendants(
                        paragraphs_in_caption[0], NodeKind.CHARACTER_RUN
                    )
                    if (name := _utf16(properties[run.record.node_id].get(1000)))
                    is not None
                ),
                None,
            )
        captions.append(
            NativeDetailedCaption(
                table_instance_id=instance,
                text=text_for(node),
                automatic_number=bool(descendants(node, NodeKind.GENERATED_TEXT)),
                style_id=style_id,
                style_name=style_name,
                page_start=first,
                page_end=last,
            )
        )
    captions.sort(key=lambda item: (item.page_start, item.table_instance_id))

    page_text = "\r\n".join(
        paragraph.text
        for paragraph in all_body_paragraphs
        if paragraph.page_start <= page <= paragraph.page_end
    )
    return NativeDetailedInspection(
        document_id=document_id,
        full_name=full_name,
        page=page,
        page_count=page_count,
        text=page_text,
        controls=tuple(controls),
        cells=tuple(cells),
        captions=tuple(captions),
        paragraphs=tuple(paragraphs),
        paragraphs_complete=paragraphs_complete,
        paragraph_scan_error=paragraph_scan_error,
        unsupported_records=(),
        inspection_errors=(),
        cell_formats=tuple(cell_formats),
    )


__all__ = ["native_detailed_inspection_from_graph"]
