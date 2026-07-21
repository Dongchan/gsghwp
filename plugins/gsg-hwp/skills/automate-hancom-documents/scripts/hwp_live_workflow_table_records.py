from __future__ import annotations

from dataclasses import dataclass

from hwp_errors import HwpLiveError
from hwp_live_structure_contract import StructureCell, StructureTable
from hwp_live_workflow_table_resolver import normalize_table_text


class TableRecordMappingError(HwpLiveError):
    pass


@dataclass(frozen=True, slots=True)
class RecordColumn:
    source_key: str
    target_column: int


@dataclass(frozen=True, slots=True)
class RecordTablePlan:
    columns: tuple[RecordColumn, ...]
    rows: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class HeaderAssignment:
    source_key: str
    target_columns: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class HeaderCandidate:
    row: int
    assignments: tuple[HeaderAssignment, ...]


def _source_key(header: StructureCell, keys: tuple[str, ...]) -> str | None:
    normalized_header = normalize_table_text(header.text)
    scores = tuple(
        (
            2
            if normalize_table_text(key) == normalized_header
            else 1
            if normalize_table_text(key) in normalized_header
            or normalized_header in normalize_table_text(key)
            else 0,
            key,
        )
        for key in keys
    )
    best = max((score for score, _ in scores), default=0)
    matches = tuple(key for score, key in scores if score == best and score > 0)
    return matches[0] if len(matches) == 1 else None


def _header_candidate(
    table: StructureTable,
    row: int,
    keys: tuple[str, ...],
) -> HeaderCandidate | None:
    headers = tuple(
        cell
        for cell in table.cells
        if cell.row == row
        and cell.owner_address == cell.address
        and cell.text.strip()
    )
    assignments = tuple(
        HeaderAssignment(
            source_key,
            tuple(range(header.column, header.column + header.column_span)),
        )
        for header in headers
        if (source_key := _source_key(header, keys)) is not None
    )
    duplicated = {
        assignment.source_key
        for assignment in assignments
        if sum(
            item.source_key == assignment.source_key
            for item in assignments
        )
        > 1
    }
    unique = tuple(
        assignment
        for assignment in assignments
        if assignment.source_key not in duplicated
    )
    if not unique or len(unique) * 2 < len(headers):
        return None
    return HeaderCandidate(row, unique)


def _run_signature(values: tuple[str, ...]) -> tuple[int, ...]:
    return tuple(
        index
        for index, value in enumerate(values)
        if index == 0 or value != values[index - 1]
    )


def _run_count(values: tuple[str, ...]) -> int:
    return len(_run_signature(values))


def plan_record_table(
    table: StructureTable,
    records: tuple[dict[str, str], ...],
) -> RecordTablePlan:
    keys = tuple(dict.fromkeys(key for record in records for key in record))
    if not keys:
        raise TableRecordMappingError("표에 입력할 레코드 필드가 비어 있습니다")
    candidates = tuple(
        candidate
        for row in range(table.rows)
        if (candidate := _header_candidate(table, row, keys)) is not None
    )
    best_count = max(
        (len(candidate.assignments) for candidate in candidates),
        default=0,
    )
    best = tuple(
        candidate
        for candidate in candidates
        if len(candidate.assignments) == best_count
    )
    if len(best) != 1:
        raise TableRecordMappingError(
            "레코드 필드와 일치하는 표 머리글 행을 정확히 하나 찾지 못했습니다"
        )
    header = best[0]
    cells_by_address = {cell.address: cell for cell in table.cells}
    owners = {
        (cell.row, cell.column): cell.owner_address
        for cell in table.cells
    }

    matched_columns = tuple(
        column
        for assignment in header.assignments
        for column in assignment.target_columns
    )
    column_runs = {
        column: _run_count(
            tuple(
                owners[(row, column)]
                for row in range(header.row + 1, table.rows)
            )
        )
        for column in matched_columns
    }
    maximum_runs = max(column_runs.values())
    identity_columns = tuple(
        column
        for column, runs in column_runs.items()
        if runs == maximum_runs
    )
    logical_rows: list[int] = []
    previous_owners: tuple[str, ...] | None = None
    for row in range(header.row + 1, table.rows):
        current_owners = tuple(owners[(row, column)] for column in identity_columns)
        if current_owners != previous_owners:
            logical_rows.append(row)
            previous_owners = current_owners
    if len(logical_rows) < len(records):
        raise TableRecordMappingError(
            "입력 레코드 수가 대상 표의 논리 데이터 행 수보다 많습니다"
        )
    rows = tuple(logical_rows[: len(records)])

    mapped = {
        assignment.source_key: assignment.target_columns[0]
        for assignment in header.assignments
        if len(assignment.target_columns) == 1
    }
    unclaimed_columns = tuple(
        column
        for column in range(table.columns)
        if column not in mapped.values()
    )
    unmatched_keys = tuple(key for key in keys if key not in mapped)
    source_by_signature: dict[tuple[int, ...], list[str]] = {}
    for key in unmatched_keys:
        signature = _run_signature(tuple(record.get(key, "") for record in records))
        source_by_signature.setdefault(signature, []).append(key)
    target_by_signature: dict[tuple[int, ...], list[int]] = {}
    for column in unclaimed_columns:
        signature = _run_signature(tuple(owners[(row, column)] for row in rows))
        target_by_signature.setdefault(signature, []).append(column)
    for signature in source_by_signature.keys() & target_by_signature.keys():
        source_keys = source_by_signature[signature]
        target_columns = target_by_signature[signature]
        if len(source_keys) != 1 or len(target_columns) != 1:
            targets_are_populated = all(
                cells_by_address[owners[(row, column)]].text.strip()
                for column in target_columns
                for row in rows
            )
            if targets_are_populated:
                continue
            raise TableRecordMappingError(
                "원본 값 구간과 병합 열 구간의 대응이 여러 가지라 자동 매핑할 수 없습니다"
            )
        mapped[source_keys[0]] = target_columns[0]

    columns = tuple(
        RecordColumn(key, mapped[key])
        for key in keys
        if key in mapped
    )
    return RecordTablePlan(columns, rows)
