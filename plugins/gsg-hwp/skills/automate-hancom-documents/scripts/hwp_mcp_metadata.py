from __future__ import annotations

from typing import Final

from mcp.server.fastmcp import FastMCP
from mcp.types import AnyFunction

from hwp_mcp_registry import McpProfile, tool_names, tool_spec


MCP_INSTRUCTIONS: Final = (
    "사용자 요청에 가장 구체적인 HWP 작업 도구를 즉시 사용합니다. "
    "각 도구는 현재 열린 저장된 편집 가능 문서에 연결하고, "
    "대상 탐색·인증 recipe 실행·결과 검증을 한 호출에서 수행합니다. "
    "일반 수정 작업에는 선행 문서 목록·connect·inspect·catalog 호출을 하지 않습니다. "
    "사용자가 열린 HWP 문서 자체의 분석이나 검증을 요청하면 hwp_render_page로 "
    "CreatePageImage PNG를 만들고 실제 내용·배치·표현을 확인합니다. 구조값도 필요한 경우 "
    "hwp_inspect_page_fast 또는 hwp_inspect_structure를 함께 사용합니다. 이 읽기 도구와 "
    "hwp_list_styles는 connect나 session_id 없이 바로 호출하며, 문서 내부 스타일을 웹 검색으로 "
    "추정하지 않습니다. hwp_inspect_page_fast가 반환한 instance_id는 hwp_add_caption·"
    "hwp_replace_image·hwp_fill_table의 직접 target_id로 사용하고, target 파라미터가 있는 "
    "표 도구에는 target: {\"target_id\": instance_id}로 전달합니다. "
    "선택한 구체 도구가 현재 클라이언트 목록에 없을 때만 hwp_execute에 "
    "그 도구명과 인자를 전달하며, 보이는 구체 도구는 계속 직접 사용합니다. "
    "도구가 바로 식별되지 않을 때만 hwp_search_tools를 한 번 사용합니다. "
    "페이지 삭제·개체 삭제·실행 취소·다시 실행은 각각 hwp_delete_page·"
    "hwp_delete_control·hwp_undo·hwp_redo를 바로 사용합니다. "
    "hwp_delete_control에 필요한 개체 ID만 hwp_inspect_page_fast로 먼저 확인합니다. "
    "현재 커서의 본문 중간에 글·표를 넣거나 특정 N쪽 다음에 새 쪽과 레이아웃을 넣을 때는 "
    "hwp_insert_layout을 사용합니다. 이미지·스크린샷을 편집 가능한 표로 재구성하기 전에는 "
    "MCP 리소스 gsg-hwp-beta://reference/native-layout을 먼저 읽고 "
    "hwp_analyze_reference_image로 전역 좌표의 선·보호 공백·텍스트 영역 증거를 만듭니다. "
    "도구가 실제 대상 후보나 누락된 사용자 입력을 반환할 때만 "
    "같은 도구를 필요한 값과 함께 다시 호출합니다. "
    "실패하거나 지원하지 않는 작업도 computer-use·Windows UI 자동화·"
    "키보드/마우스·Python COM으로 우회하지 않습니다. "
    "이미지나 슬라이드의 양식을 다시 만들라는 요청은 페이지 통이미지를 붙이지 않고 "
    "편집 가능한 문단·표·개별 그림으로 재구성합니다."
)


def register_public_tools(
    server: FastMCP[None],
    tools: tuple[AnyFunction, ...],
    profile: McpProfile,
) -> None:
    allowed = tool_names(profile)
    for tool in tools:
        if tool.__name__ in allowed:
            server.add_tool(tool, description=tool_spec(tool.__name__).description)
