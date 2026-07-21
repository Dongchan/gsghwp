from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from os import environ
from types import MappingProxyType
from typing import Final, Literal

from hwp_mcp_search import rank_tool_specs
from hwp_operation_contract import HwpWorkflowId
from hwp_public_action_metadata import (
    HWP_ADD_CAPTION_DESCRIPTION,
    HWP_APPEND_EXCEL_TABLE_DESCRIPTION,
    HWP_APPEND_LAYOUT_DESCRIPTION,
    HWP_APPEND_REPORT_DESCRIPTION,
    HWP_APPLY_STYLE_DESCRIPTION,
    HWP_DELETE_CONTROL_DESCRIPTION,
    HWP_DELETE_PAGE_DESCRIPTION,
    HWP_FILL_TABLE_DESCRIPTION,
    HWP_FORMAT_TEXT_DESCRIPTION,
    HWP_INSERT_IMAGE_DESCRIPTION,
    HWP_REDO_DESCRIPTION,
    HWP_REPLACE_IMAGE_DESCRIPTION,
    HWP_REPLACE_SELECTED_TEXT_DESCRIPTION,
    HWP_SAVE_REOPEN_VERIFY_DESCRIPTION,
    HWP_UNDO_DESCRIPTION,
)
from hwp_public_table_metadata import (
    HWP_BUILD_TABLE_SERIES_DESCRIPTION,
    HWP_EXPAND_AND_FILL_TABLE_DESCRIPTION,
    HWP_FILL_TABLE_IMAGES_DESCRIPTION,
    HWP_FORMAT_TABLE_DESCRIPTION,
    HWP_MERGE_TABLE_CELLS_DESCRIPTION,
    HWP_REPEAT_TABLE_TEMPLATE_DESCRIPTION,
    HWP_SPLIT_TABLE_CELL_DESCRIPTION,
    HWP_SYNC_VISIBILITY_ANALYSIS_TABLES_DESCRIPTION,
)


type McpProfile = Literal["production", "qa"]
type ExecutionPath = Literal[
    "native_required",
    "hybrid_python_native_preferred",
    "hybrid_event_com",
    "rot_com",
    "com_dispatch",
    "python_catalog",
    "mcp_forward",
    "win32_ui",
]
type CapabilityCategory = Literal[
    "catalog",
    "diagnostic",
    "document",
    "image",
    "layout",
    "office",
    "official_api",
    "state",
    "structure",
    "style",
    "table",
    "text",
    "verification",
]
type CapabilityOperation = Literal["read", "write", "session"]


_ALL_PROFILES: Final = frozenset[McpProfile](("production", "qa"))
_QA_ONLY: Final = frozenset[McpProfile](("qa",))


@dataclass(frozen=True, slots=True)
class McpToolSpec:
    name: str
    description: str
    category: CapabilityCategory
    operation: CapabilityOperation
    execution_path: ExecutionPath
    requires_session: bool = True
    requires_state_token: bool = False
    profiles: frozenset[McpProfile] = _QA_ONLY
    workflow: HwpWorkflowId | None = None


class McpProfileError(RuntimeError):
    value: str

    def __init__(self, value: str) -> None:
        super().__init__(f"지원하지 않는 HANCOM_MCP_PROFILE입니다: {value}")
        self.value = value


class McpToolSearchError(RuntimeError):
    field: str

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


MCP_TOOL_SPECS: Final = (
    McpToolSpec("hwp_list_open_documents", "새 HWP를 띄우지 않고 ROT의 열린 문서를 나열합니다.", "document", "read", "rot_com", requires_session=False, profiles=_ALL_PROFILES),
    McpToolSpec("hwp_connect", "선택자를 생략하면 활성 HWP에 연결하고, 이미 같은 문서에 연결됐으면 기존 session_id를 그대로 반환합니다.", "document", "session", "rot_com", requires_session=False, profiles=_ALL_PROFILES),
    McpToolSpec("hwp_inspect", "connect나 session_id 선행 작업 없이 현재 쪽·커서·선택·글자·문단·용지 모양을 읽습니다.", "state", "read", "com_dispatch", requires_session=False, profiles=_ALL_PROFILES),
    McpToolSpec("hwp_inspect_page_fast", "connect나 session_id 없이 표·개체의 크기와 표 앵커 문단 모양을 C++/ATL로 즉시 읽습니다. 반환된 instance_id는 hwp_add_caption·hwp_replace_image·hwp_fill_table의 직접 target_id로, target 파라미터가 있는 표 도구에서는 target: {\"target_id\": instance_id}로 재사용하며, include_cells=true일 때 셀·병합·내용과 각 셀의 실제 너비·높이까지 조회합니다.", "structure", "read", "native_required", requires_session=False, profiles=_ALL_PROFILES),
    McpToolSpec("hwp_inspect_structure", "connect나 session_id 없이 지정 쪽의 문단·개체 크기·표·셀·병합·그림·캡션·쪽 범위와 상태 토큰을 읽습니다.", "structure", "read", "native_required", requires_session=False, profiles=_ALL_PROFILES),
    McpToolSpec("hwp_watch_state", "revision 이후 문서 구조·커서·선택·창·대화상자 변경점을 기다려 반환합니다.", "state", "read", "hybrid_event_com"),
    McpToolSpec("hwp_inspect_window_state", "지정한 한/글 창의 모달·오류 대화상자 구조를 즉시 반환합니다.", "diagnostic", "read", "win32_ui", requires_session=False, profiles=_QA_ONLY),
    McpToolSpec("hwp_dismiss_dialogs", "기록한 한/글 모달 대화상자를 닫고 전후 구조를 반환합니다.", "diagnostic", "write", "win32_ui", requires_session=False, profiles=_QA_ONLY),
    McpToolSpec("hwp_list_styles", "connect나 session_id 없이 현재 문서의 문단 스타일 이름과 ID를 읽습니다.", "style", "read", "com_dispatch", requires_session=False, profiles=_ALL_PROFILES),
    McpToolSpec("hwp_operate", "현재 열린 저장 가능한 HWP에서 사용자의 요청을 가장 구체적인 인증 작업으로 즉시 수행합니다. 선행 list/connect/inspect/catalog 호출 없이 한 호출 안에서 연결, 대상 확정, native 실행, 검증을 처리합니다. needs_input이거나 실제 대상 후보가 여러 개일 때만 질문하며 공식 API 이름, 셀 주소, 컨트롤 ID를 추측하지 않습니다.", "official_api", "write", "native_required", profiles=_QA_ONLY),
    McpToolSpec("hwp_fill_table", HWP_FILL_TABLE_DESCRIPTION, "table", "write", "native_required", profiles=_ALL_PROFILES, workflow="table.fill_existing"),
    McpToolSpec("hwp_expand_and_fill_table", HWP_EXPAND_AND_FILL_TABLE_DESCRIPTION, "table", "write", "native_required", profiles=_ALL_PROFILES, workflow="table.expand_and_fill"),
    McpToolSpec("hwp_repeat_table_template", HWP_REPEAT_TABLE_TEMPLATE_DESCRIPTION, "table", "write", "native_required", profiles=_ALL_PROFILES, workflow="table.repeat_template"),
    McpToolSpec("hwp_insert_image", HWP_INSERT_IMAGE_DESCRIPTION, "image", "write", "native_required", profiles=_ALL_PROFILES, workflow="image.insert"),
    McpToolSpec("hwp_replace_image", HWP_REPLACE_IMAGE_DESCRIPTION, "image", "write", "native_required", profiles=_ALL_PROFILES, workflow="image.replace"),
    McpToolSpec("hwp_build_table_series", HWP_BUILD_TABLE_SERIES_DESCRIPTION, "table", "write", "native_required", profiles=_ALL_PROFILES, workflow="table.build_series"),
    McpToolSpec("hwp_sync_visibility_analysis_tables", HWP_SYNC_VISIBILITY_ANALYSIS_TABLES_DESCRIPTION, "table", "write", "native_required", profiles=_ALL_PROFILES, workflow="table.build_series"),
    McpToolSpec("hwp_fill_table_images", HWP_FILL_TABLE_IMAGES_DESCRIPTION, "image", "write", "native_required", profiles=_ALL_PROFILES, workflow="table.insert_images"),
    McpToolSpec("hwp_add_caption", HWP_ADD_CAPTION_DESCRIPTION, "text", "write", "native_required", profiles=_ALL_PROFILES, workflow="caption.add"),
    McpToolSpec("hwp_copy_style", "원본 위치의 글자·문단 스타일을 대상 위치에 복사합니다. style.copy 인증 recipe로 실행합니다.", "style", "write", "native_required", profiles=_QA_ONLY, workflow="style.copy"),
    McpToolSpec("hwp_apply_style", HWP_APPLY_STYLE_DESCRIPTION, "style", "write", "native_required", profiles=_ALL_PROFILES, workflow="style.apply"),
    McpToolSpec("hwp_format_text", HWP_FORMAT_TEXT_DESCRIPTION, "text", "write", "native_required", profiles=_ALL_PROFILES, workflow="text.format"),
    McpToolSpec("hwp_replace_selected_text", HWP_REPLACE_SELECTED_TEXT_DESCRIPTION, "text", "write", "native_required", profiles=_ALL_PROFILES, workflow="document.replace_selection"),
    McpToolSpec("hwp_format_table", HWP_FORMAT_TABLE_DESCRIPTION, "table", "write", "native_required", profiles=_ALL_PROFILES, workflow="table.format"),
    McpToolSpec("hwp_merge_table_cells", HWP_MERGE_TABLE_CELLS_DESCRIPTION, "table", "write", "native_required", profiles=_ALL_PROFILES, workflow="table.merge_cells"),
    McpToolSpec("hwp_split_table_cell", HWP_SPLIT_TABLE_CELL_DESCRIPTION, "table", "write", "native_required", profiles=_ALL_PROFILES, workflow="table.split_cells"),
    McpToolSpec("hwp_delete_page", HWP_DELETE_PAGE_DESCRIPTION, "document", "write", "native_required", profiles=_ALL_PROFILES, workflow="document.delete_page"),
    McpToolSpec("hwp_delete_control", HWP_DELETE_CONTROL_DESCRIPTION, "structure", "write", "native_required", profiles=_ALL_PROFILES, workflow="control.delete"),
    McpToolSpec("hwp_undo", HWP_UNDO_DESCRIPTION, "document", "write", "native_required", profiles=_ALL_PROFILES, workflow="document.undo"),
    McpToolSpec("hwp_redo", HWP_REDO_DESCRIPTION, "document", "write", "native_required", profiles=_ALL_PROFILES, workflow="document.redo"),
    McpToolSpec("hwp_append_layout", HWP_APPEND_LAYOUT_DESCRIPTION, "layout", "write", "native_required", profiles=_ALL_PROFILES, workflow="document.append_layout"),
    McpToolSpec("hwp_append_report", HWP_APPEND_REPORT_DESCRIPTION, "layout", "write", "native_required", profiles=_ALL_PROFILES),
    McpToolSpec("hwp_append_excel_table", HWP_APPEND_EXCEL_TABLE_DESCRIPTION, "office", "write", "hybrid_python_native_preferred", profiles=_ALL_PROFILES),
    McpToolSpec("hwp_save_reopen_verify", HWP_SAVE_REOPEN_VERIFY_DESCRIPTION, "verification", "write", "native_required", profiles=_ALL_PROFILES, workflow="document.save_reopen_verify"),
    McpToolSpec("hwp_run_official_api_batch", "현재 세션 문서에서 공식 API 인덱스 범위를 C++/ATL로 실행합니다.", "official_api", "write", "native_required", profiles=_QA_ONLY),
    McpToolSpec("hwp_probe_official_api_batch", "지정한 한컴 창에서 공식 API 인덱스 범위를 C++/ATL로 진단합니다.", "official_api", "write", "native_required", requires_session=False, profiles=_QA_ONLY),
    McpToolSpec("hwp_probe_official_api_payload", "검증 하네스의 HCV1 요청을 지정한 한컴 창의 C++/ATL 진단기에 전달합니다.", "official_api", "write", "native_required", requires_session=False, profiles=_QA_ONLY),
    McpToolSpec("hwp_replace_selection", "예상 좌표와 원문을 검증해 선택 텍스트를 C++/ATL 내부에서 교체합니다.", "text", "write", "native_required"),
    McpToolSpec("hwp_apply_layout", "현재 커서 또는 plan.target=document_end에 문단·표·캡션·원본 비율 그림을 한 번의 C++/ATL 배치로 삽입합니다.", "layout", "write", "native_required"),
    McpToolSpec("hwp_update_table_cells", "표 참조값·상태 토큰·셀 원문을 검증한 뒤 기존 셀을 채웁니다.", "table", "write", "native_required", requires_state_token=True),
    McpToolSpec("hwp_insert_table_images", "현재 열린 HWP의 지정 표 셀에 주소별 그림을 삽입합니다.", "image", "write", "native_required", profiles=_QA_ONLY, workflow="table.insert_images"),
    McpToolSpec("hwp_import_office_table", "PPTX 또는 XLSX 표 값을 읽어 기존 한컴 표를 네이티브 배치로 채웁니다.", "office", "write", "hybrid_python_native_preferred", requires_state_token=True),
    McpToolSpec("hwp_propagate_table_cells", "원본 표 셀을 여러 반복 표의 대응 셀에 네이티브 배치로 채웁니다.", "table", "write", "native_required", requires_state_token=True),
    McpToolSpec("hwp_insert_folder_images", "파일명 키로 그림을 찾아 여러 표 셀에 원본 비율로 넣습니다.", "image", "write", "native_required", requires_state_token=True),
    McpToolSpec("hwp_rebuild_document", "원본을 덮어쓰지 않고 HWPX 구조 왕복 후 새 HWP를 만들고 검증합니다.", "document", "write", "com_dispatch", requires_session=False),
    McpToolSpec("hwp_render_page", "문서 상태를 유지한 채 CreatePageImage로 현재 또는 지정 쪽 PNG를 만듭니다. 열린 HWP 문서의 내용·배치·표현을 분석하거나 결과를 검증할 때 이 렌더를 사용합니다.", "verification", "read", "com_dispatch", requires_session=False, profiles=_ALL_PROFILES),
    McpToolSpec("hwp_disconnect", "HWP를 종료하지 않고 연결 참조만 해제합니다.", "document", "session", "rot_com", profiles=_ALL_PROFILES),
    McpToolSpec("hwp_get_capabilities", "현재 프로필의 도구를 기능·읽기/쓰기·실행 경로별로 반환합니다.", "catalog", "read", "python_catalog", requires_session=False, profiles=_ALL_PROFILES),
    McpToolSpec("hwp_search_tools", "한컴 작업 설명을 검색해 바로 사용할 구체적인 MCP 도구를 짧은 목록으로 반환합니다.", "catalog", "read", "python_catalog", requires_session=False, profiles=_ALL_PROFILES),
    McpToolSpec("hwp_runtime_info", "현재 MCP 버전·프로세스·소스 경로·도구 스키마 해시와 소스 갱신 필요 여부를 반환합니다.", "diagnostic", "read", "python_catalog", requires_session=False, profiles=_ALL_PROFILES),
    McpToolSpec("hwp_execute", "현재 클라이언트의 도구 목록이 오래되어 선택한 구체 HWP 도구가 보이지 않을 때, 설치 서버에 등록된 정확한 도구명과 인자를 그대로 전달합니다. 일반 작업에서는 보이는 구체 도구를 직접 사용합니다.", "catalog", "write", "mcp_forward", requires_session=False, profiles=_ALL_PROFILES),
    McpToolSpec("hwp_search_official_api", "공식 2025-04 API 카탈로그에서 이름·설명·선언·근거 쪽을 검색합니다.", "catalog", "read", "python_catalog", requires_session=False),
    McpToolSpec("hwp_get_official_api_coverage", "공식 API를 네이티브 실행 가능·정확한 매핑·인덱스 전용으로 분류합니다.", "catalog", "read", "python_catalog", requires_session=False),
)

_TOOL_BY_NAME: Final[Mapping[str, McpToolSpec]] = MappingProxyType(
    {spec.name: spec for spec in MCP_TOOL_SPECS}
)


def configured_mcp_profile(arguments: Sequence[str] = ()) -> McpProfile:
    if arguments:
        if len(arguments) != 2 or arguments[0] != "--profile":
            raise McpProfileError(" ".join(arguments))
        value = arguments[1]
    else:
        value = environ.get("HANCOM_MCP_PROFILE", "production")
    if value == "production" or value == "qa":
        return value
    raise McpProfileError(value)


def tool_spec(name: str) -> McpToolSpec:
    return _TOOL_BY_NAME[name]


def tool_specs(profile: McpProfile) -> tuple[McpToolSpec, ...]:
    return tuple(spec for spec in MCP_TOOL_SPECS if profile in spec.profiles)


def tool_names(profile: McpProfile) -> frozenset[str]:
    return frozenset(spec.name for spec in tool_specs(profile))


def search_tool_specs(
    query: str,
    *,
    profile: McpProfile,
    category: CapabilityCategory | None = None,
    operation: CapabilityOperation | None = None,
    limit: int = 10,
) -> tuple[McpToolSpec, ...]:
    if not query.strip():
        raise McpToolSearchError("query", "tool search query must not be blank")
    if limit < 1 or limit > 50:
        raise McpToolSearchError(
            "limit",
            "tool search limit must be between 1 and 50",
        )
    candidates = tuple(
        spec
        for spec in tool_specs(profile)
        if (category is None or spec.category == category)
        and (operation is None or spec.operation == operation)
    )
    return rank_tool_specs(query, candidates, limit=limit)
