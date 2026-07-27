from __future__ import annotations

from base64 import b64decode, b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from hwp_errors import HwpLiveError


# Native source: LiveInspection.cpp::ParseRequestedPages hard-codes this page limit.
NATIVE_INSPECT_PAGES_MAX: Final = 256
# Native source: BatchProtocol.cpp::kMaximumOperations defines this operation limit.
NATIVE_BATCH_MAX_OPERATIONS: Final = 5_000


@dataclass(frozen=True, slots=True)
class NativeTextCell:
    address: str
    expected_text: str
    replacement: str


@dataclass(frozen=True, slots=True)
class NativeImageCell:
    address: str
    expected_text: str
    path: Path


type NativeCellOperation = NativeTextCell | NativeImageCell


@dataclass(frozen=True, slots=True)
class NativeTableBatch:
    control_id: str
    operations: tuple[NativeCellOperation, ...]


@dataclass(frozen=True, slots=True)
class NativeBatchRequest:
    document_id: int
    full_name: str
    tables: tuple[NativeTableBatch, ...]


@dataclass(frozen=True, slots=True)
class NativeBatchResult:
    text_updates: int
    image_updates: int
    elapsed_microseconds: int
    validation_microseconds: int = 0
    locate_microseconds: int = 0
    text_microseconds: int = 0
    image_microseconds: int = 0
    verify_microseconds: int = 0
    image_timing_count: int = 0
    image_max_microseconds: int = 0
    image_total_microseconds: int = 0


@dataclass(frozen=True, slots=True)
class NativeLifecycleResult:
    verified: bool
    reopened_path: str | None
    before_page_count: int | None
    before_modified: bool | None
    before_control_count: int | None
    before_control_hash: str | None
    before_text_hash: str | None
    before_document_hash: str | None
    save_hresult: int
    save_return: int
    post_save_modified: bool | None
    clear_hresult: int
    clear_return: int
    open_hresult: int
    open_return: int
    recovered: bool
    recovery_hresult: int
    recovery_return: int
    after_page_count: int | None
    after_modified: bool | None
    after_control_count: int | None
    after_control_hash: str | None
    after_text_hash: str | None
    after_document_hash: str | None
    elapsed_microseconds: int
    before_document_length: int | None = None
    after_document_length: int | None = None
    before_document_section_hashes: tuple[str, ...] | None = None
    after_document_section_hashes: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class NativeSaveResult:
    verified: bool
    saved_path: str | None
    before_page_count: int | None
    before_modified: bool | None
    before_control_count: int | None
    before_control_hash: str | None
    before_text_hash: str | None
    before_document_hash: str | None
    save_hresult: int
    save_return: int
    post_save_modified: bool | None
    after_page_count: int | None
    after_modified: bool | None
    after_control_count: int | None
    after_control_hash: str | None
    after_text_hash: str | None
    after_document_hash: str | None
    file_size: int
    file_write_time_100ns: int | None
    elapsed_microseconds: int
    before_document_length: int | None = None
    after_document_length: int | None = None
    before_document_section_hashes: tuple[str, ...] | None = None
    after_document_section_hashes: tuple[str, ...] | None = None


def _encode(value: str) -> str:
    encoded = b64encode(value.encode("utf-8")).decode("ascii")
    return encoded or " "


def _decode(value: str) -> str:
    try:
        return b64decode(value, validate=True).decode("utf-8")
    except (UnicodeDecodeError, ValueError) as error:
        raise HwpLiveError("네이티브 배치 응답 문자열을 해석하지 못했습니다") from error


def _plain(value: str, label: str) -> str:
    if not value or any(character in value for character in "\t\r\n"):
        raise HwpLiveError(f"네이티브 배치 {label} 형식이 올바르지 않습니다")
    return value


def _absolute_path(path: Path) -> str:
    return str(path if path.is_absolute() else path.absolute())


def encode_batch_request(request: NativeBatchRequest) -> str:
    if request.document_id < 0 or not request.tables:
        raise HwpLiveError("네이티브 배치 문서 식별값이 올바르지 않습니다")
    operation_count = sum(len(table.operations) for table in request.tables)
    if operation_count > NATIVE_BATCH_MAX_OPERATIONS:
        limit_message = f"네이티브 배치 요청이 {NATIVE_BATCH_MAX_OPERATIONS}개 작업 제한을 초과했습니다."
        atomicity_message = (
            "부분 변경을 막고 원자성을 보존하기 위해 요청을 분할하지 않습니다"
        )
        raise HwpLiveError(f"{limit_message} {atomicity_message}")
    lines = ["HCB1", f"DOC\t{request.document_id}\t{_encode(request.full_name)}"]
    for table in request.tables:
        if not table.operations:
            raise HwpLiveError("네이티브 배치에서 비어 있는 표 작업은 허용하지 않습니다")
        lines.append(f"TABLE\t{_plain(table.control_id, '표 식별값')}")
        for operation in table.operations:
            address = _plain(operation.address, "셀 주소")
            if isinstance(operation, NativeTextCell):
                lines.append(
                    "\t".join(
                        (
                            "TEXT",
                            address,
                            _encode(operation.expected_text),
                            _encode(operation.replacement),
                        )
                    )
                )
            else:
                lines.append(
                    "\t".join(
                        (
                            "IMAGE",
                            address,
                            _encode(operation.expected_text),
                            _encode(_absolute_path(operation.path)),
                        )
                    )
                )
        lines.append("ENDTABLE")
    lines.append("END")
    payload = "\n".join(lines)
    if len(payload) > 8_000_000:
        raise HwpLiveError("네이티브 배치 요청이 8MB 제한을 초과했습니다")
    return payload


def decode_batch_result(payload: str) -> NativeBatchResult:
    fields = payload.split("\t")
    if len(fields) in {5, 13} and fields[:2] == ["HCB1", "OK"]:
        try:
            values = tuple(int(value) for value in fields[2:])
        except ValueError as error:
            raise HwpLiveError("네이티브 배치 성공 응답 숫자가 올바르지 않습니다") from error
        if min(values) < 0:
            raise HwpLiveError("네이티브 배치 성공 응답에 음수가 있습니다")
        if len(values) == 3:
            return NativeBatchResult(values[0], values[1], values[2])
        if values[8] != values[1] or values[9] > values[10]:
            raise HwpLiveError("네이티브 배치 그림 타이밍 응답이 일관되지 않습니다")
        return NativeBatchResult(
            text_updates=values[0],
            image_updates=values[1],
            elapsed_microseconds=values[2],
            validation_microseconds=values[3],
            locate_microseconds=values[4],
            text_microseconds=values[5],
            image_microseconds=values[6],
            verify_microseconds=values[7],
            image_timing_count=values[8],
            image_max_microseconds=values[9],
            image_total_microseconds=values[10],
        )
    if len(fields) == 5 and fields[:2] == ["HCB1", "ERROR"]:
        code = _plain(fields[2], "오류 코드")
        address = _decode(fields[3])
        message = _decode(fields[4])
        location = f" {address}" if address else ""
        raise HwpLiveError(f"네이티브 배치 {code}{location}: {message}")
    raise HwpLiveError("네이티브 배치 응답 형식이 올바르지 않습니다")


def _lifecycle_integer(value: str, label: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise HwpLiveError(f"네이티브 저장·재개방 {label} 숫자가 올바르지 않습니다") from error


def _lifecycle_boolean(value: str, label: str) -> bool:
    if value == "0":
        return False
    if value == "1":
        return True
    raise HwpLiveError(f"네이티브 저장·재개방 {label} 불리언 값이 올바르지 않습니다")


def _lifecycle_optional_boolean(value: str, label: str) -> bool | None:
    if value == "-1":
        return None
    return _lifecycle_boolean(value, label)


def _lifecycle_optional_nonnegative(value: str, label: str) -> int | None:
    result = _lifecycle_integer(value, label)
    if result == -1:
        return None
    if result < 0:
        raise HwpLiveError(f"네이티브 저장·재개방 {label} 값이 올바르지 않습니다")
    return result


def _lifecycle_return(value: str, label: str) -> int:
    result = _lifecycle_integer(value, label)
    if result not in {-1, 0, 1}:
        raise HwpLiveError(f"네이티브 저장·재개방 {label} 반환값이 올바르지 않습니다")
    return result


def _diagnostic_nonnegative_integer(value: str, label: str) -> int:
    if (
        not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
    ):
        raise HwpLiveError(
            f"네이티브 저장·재개방 {label} 진단 숫자가 올바르지 않습니다"
        )
    result = int(value)
    if result > 0xFFFF_FFFF_FFFF_FFFF:
        raise HwpLiveError(
            f"네이티브 저장·재개방 {label} 진단 숫자가 올바르지 않습니다"
        )
    return result


def _document_fingerprint_diagnostics(
    document_length_value: str,
    sections_value: str,
    label: str,
) -> tuple[int, tuple[str, ...]]:
    document_length = _diagnostic_nonnegative_integer(
        document_length_value,
        f"{label} 문서 길이",
    )
    section_hashes = tuple(sections_value.split(",")) if sections_value else ()
    expected_section_count = (document_length + 65_535) // 65_536
    if len(section_hashes) != expected_section_count:
        raise HwpLiveError(
            f"네이티브 저장·재개방 {label} 구간 해시 수가 일치하지 않습니다"
        )
    if any(
        len(section_hash) != 16
        or not section_hash.isascii()
        or any(character not in "0123456789abcdef" for character in section_hash)
        for section_hash in section_hashes
    ):
        raise HwpLiveError(
            f"네이티브 저장·재개방 {label} 구간 해시 형식이 올바르지 않습니다"
        )
    return document_length, section_hashes


def _optional_document_fingerprint_diagnostics(
    fields: list[str],
    base_field_count: int,
) -> tuple[
    int | None,
    int | None,
    tuple[str, ...] | None,
    tuple[str, ...] | None,
]:
    if len(fields) == base_field_count:
        return None, None, None, None
    before_length, before_section_hashes = _document_fingerprint_diagnostics(
        fields[base_field_count],
        fields[base_field_count + 2],
        "저장 전",
    )
    after_length, after_section_hashes = _document_fingerprint_diagnostics(
        fields[base_field_count + 1],
        fields[base_field_count + 3],
        "저장 후",
    )
    return (
        before_length,
        after_length,
        before_section_hashes,
        after_section_hashes,
    )


def decode_lifecycle_result(payload: str) -> NativeLifecycleResult:
    fields = payload.split("\t")
    if len(fields) not in {26, 30} or fields[0] != "HCL12":
        raise HwpLiveError("네이티브 저장·재개방 응답 형식이 올바르지 않습니다")
    (
        before_document_length,
        after_document_length,
        before_document_section_hashes,
        after_document_section_hashes,
    ) = _optional_document_fingerprint_diagnostics(fields, 26)
    reopened_path = _decode(fields[2]) or None
    before_page_count = _lifecycle_optional_nonnegative(fields[3], "저장 전 쪽 수")
    before_control_count = _lifecycle_optional_nonnegative(fields[5], "저장 전 개체 수")
    after_page_count = _lifecycle_optional_nonnegative(fields[19], "재개방 후 쪽 수")
    after_control_count = _lifecycle_optional_nonnegative(fields[21], "재개방 후 개체 수")
    elapsed_microseconds = _lifecycle_integer(fields[25], "처리 시간")
    for page_count in (before_page_count, after_page_count):
        if page_count == 0:
            raise HwpLiveError("네이티브 저장·재개방 쪽 수는 1 이상이어야 합니다")
    if elapsed_microseconds < 0:
        raise HwpLiveError("네이티브 저장·재개방 처리 시간이 음수입니다")
    before_control_hash = fields[6] or None
    after_control_hash = fields[22] or None
    if any(
        character in value
        for value in (before_control_hash, after_control_hash)
        if value is not None
        for character in "\r\n"
    ):
        raise HwpLiveError("네이티브 저장·재개방 개체 해시 형식이 올바르지 않습니다")
    result = NativeLifecycleResult(
        verified=_lifecycle_boolean(fields[1], "검증 상태"),
        reopened_path=reopened_path,
        before_page_count=before_page_count,
        before_modified=_lifecycle_optional_boolean(fields[4], "저장 전 수정 상태"),
        before_control_count=before_control_count,
        before_control_hash=before_control_hash,
        before_text_hash=_lifecycle_hash(fields[7], "저장 전 본문"),
        before_document_hash=_lifecycle_hash(fields[8], "저장 전 문서"),
        save_hresult=_lifecycle_integer(fields[9], "Save HRESULT"),
        save_return=_lifecycle_return(fields[10], "Save"),
        post_save_modified=_lifecycle_optional_boolean(fields[11], "저장 직후 수정 상태"),
        clear_hresult=_lifecycle_integer(fields[12], "Clear HRESULT"),
        clear_return=_lifecycle_return(fields[13], "Clear"),
        open_hresult=_lifecycle_integer(fields[14], "Open HRESULT"),
        open_return=_lifecycle_return(fields[15], "Open"),
        recovered=_lifecycle_boolean(fields[16], "세션 복구 상태"),
        recovery_hresult=_lifecycle_integer(fields[17], "복구 HRESULT"),
        recovery_return=_lifecycle_return(fields[18], "복구"),
        after_page_count=after_page_count,
        after_modified=_lifecycle_optional_boolean(fields[20], "재개방 후 수정 상태"),
        after_control_count=after_control_count,
        after_control_hash=after_control_hash,
        after_text_hash=_lifecycle_hash(fields[23], "재개방 후 본문"),
        after_document_hash=_lifecycle_hash(fields[24], "재개방 후 문서"),
        elapsed_microseconds=elapsed_microseconds,
        before_document_length=before_document_length,
        after_document_length=after_document_length,
        before_document_section_hashes=before_document_section_hashes,
        after_document_section_hashes=after_document_section_hashes,
    )
    fingerprint_matches = (
        result.before_page_count == result.after_page_count
        and result.before_control_count == result.after_control_count
        and result.before_control_hash == result.after_control_hash
        and result.before_text_hash == result.after_text_hash
        and result.before_document_hash == result.after_document_hash
        and result.before_text_hash is not None
        and result.before_document_hash is not None
    )
    if result.verified and not (
        result.reopened_path is not None
        and result.save_hresult >= 0
        and (
            result.save_return == 1
            or (result.before_modified is False and result.save_return == 0)
        )
        and result.clear_hresult >= 0
        and result.open_hresult >= 0
        and result.open_return == 1
        and result.post_save_modified is False
        and result.after_modified is False
        and not result.recovered
        and fingerprint_matches
    ):
        raise HwpLiveError("네이티브 저장·재개방 성공 응답의 readback 근거가 일치하지 않습니다")
    if result.recovered and not (
        not result.verified
        and result.reopened_path is not None
        and result.recovery_hresult >= 0
        and result.recovery_return == 1
        and result.post_save_modified is False
        and result.after_modified is False
        and fingerprint_matches
    ):
        raise HwpLiveError("네이티브 저장·재개방 복구 응답의 문서 지문이 일치하지 않습니다")
    return result


def _lifecycle_hash(value: str, label: str) -> str | None:
    if value == "0":
        return None
    if not value.isdecimal():
        raise HwpLiveError(f"네이티브 저장 {label} 해시 형식이 올바르지 않습니다")
    return value


def decode_save_result(payload: str) -> NativeSaveResult:
    fields = payload.split("\t")
    if len(fields) not in {20, 21, 25} or fields[0] != "HLS1":
        raise HwpLiveError("네이티브 일반 저장 응답 형식이 올바르지 않습니다")
    base_field_count = 21 if len(fields) == 25 else len(fields)
    (
        before_document_length,
        after_document_length,
        before_document_section_hashes,
        after_document_section_hashes,
    ) = _optional_document_fingerprint_diagnostics(fields, base_field_count)
    before_page_count = _lifecycle_optional_nonnegative(fields[3], "저장 전 쪽 수")
    before_control_count = _lifecycle_optional_nonnegative(fields[5], "저장 전 개체 수")
    after_page_count = _lifecycle_optional_nonnegative(fields[12], "저장 후 쪽 수")
    after_control_count = _lifecycle_optional_nonnegative(fields[14], "저장 후 개체 수")
    file_size = _lifecycle_integer(fields[18], "파일 크기")
    file_write_time_100ns = (
        None
        if base_field_count == 20
        else _lifecycle_integer(fields[19], "파일 수정 시각")
    )
    elapsed_microseconds = _lifecycle_integer(
        fields[base_field_count - 1],
        "처리 시간",
    )
    for page_count in (before_page_count, after_page_count):
        if page_count == 0:
            raise HwpLiveError("네이티브 일반 저장 쪽 수는 1 이상이어야 합니다")
    if (
        file_size < 0
        or (file_write_time_100ns is not None and file_write_time_100ns < 0)
        or elapsed_microseconds < 0
    ):
        raise HwpLiveError("네이티브 일반 저장 크기 또는 처리 시간이 음수입니다")
    result = NativeSaveResult(
        verified=_lifecycle_boolean(fields[1], "검증 상태"),
        saved_path=_decode(fields[2]) or None,
        before_page_count=before_page_count,
        before_modified=_lifecycle_optional_boolean(fields[4], "저장 전 수정 상태"),
        before_control_count=before_control_count,
        before_control_hash=_lifecycle_hash(fields[6], "저장 전 개체"),
        before_text_hash=_lifecycle_hash(fields[7], "저장 전 본문"),
        before_document_hash=_lifecycle_hash(fields[8], "저장 전 문서"),
        save_hresult=_lifecycle_integer(fields[9], "Save HRESULT"),
        save_return=_lifecycle_return(fields[10], "Save"),
        post_save_modified=_lifecycle_optional_boolean(fields[11], "저장 직후 수정 상태"),
        after_page_count=after_page_count,
        after_modified=_lifecycle_optional_boolean(fields[13], "저장 후 수정 상태"),
        after_control_count=after_control_count,
        after_control_hash=_lifecycle_hash(fields[15], "저장 후 개체"),
        after_text_hash=_lifecycle_hash(fields[16], "저장 후 본문"),
        after_document_hash=_lifecycle_hash(fields[17], "저장 후 문서"),
        file_size=file_size,
        file_write_time_100ns=file_write_time_100ns,
        elapsed_microseconds=elapsed_microseconds,
        before_document_length=before_document_length,
        after_document_length=after_document_length,
        before_document_section_hashes=before_document_section_hashes,
        after_document_section_hashes=after_document_section_hashes,
    )
    if result.verified and not (
        result.saved_path is not None
        and result.save_hresult >= 0
        and (
            result.save_return == 1
            or (result.before_modified is False and result.save_return == 0)
        )
        and result.post_save_modified is False
        and result.after_modified is False
        and result.before_page_count == result.after_page_count
        and result.before_control_count == result.after_control_count
        and result.before_control_hash == result.after_control_hash
        and result.before_text_hash is not None
        and result.before_text_hash == result.after_text_hash
        and result.before_document_hash is not None
        and result.before_document_hash == result.after_document_hash
        and result.file_size > 0
        and (
            result.file_write_time_100ns is None
            or result.file_write_time_100ns > 0
        )
    ):
        raise HwpLiveError("네이티브 일반 저장 성공 응답의 readback 근거가 일치하지 않습니다")
    return result
