from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from sys import argv, stderr
from typing import Final

from mcp.server.fastmcp import FastMCP
from pydantic import JsonValue

from hwp_custom_action_runtime import (
    CustomActionClickRuntime,
    maintain_custom_action_clicks,
)
from hwp_custom_action_tools import HwpCustomActionTools
from hwp_live_bridge import HancomBridge
from hwp_live_preview_maintenance import maintain_live_previews
from hwp_live_session import LiveHwpController
from hwp_codex_skill_install import startup_codex_skill_registration_record
from hwp_mcp_dispatch import McpThreadDispatcher
from hwp_mcp_document_wrappers import McpDocumentRecipeWrappers
from hwp_mcp_forward import ForwardingFastMCP, HwpExecuteArguments
from hwp_mcp_metadata import MCP_INSTRUCTIONS
from hwp_mcp_operation import McpOperationHandler
from hwp_mcp_operation_executor import HwpOperationExecutor
from hwp_mcp_pageplan_tools import HwpPagePlanTools
from hwp_mcp_qa_handlers import McpQaHandlers
from hwp_mcp_registration import McpToolBindings, register_mcp_tools
from hwp_mcp_resources import register_guidance_resources
from hwp_mcp_registry import McpProfile, configured_mcp_profile
from hwp_native_graph_cache import NativeGraphCache
from hwp_native_graph_client import connected_native_graph_client
from hwp_native_install import ensure_native_bridge_registered
from hwp_operation_journal import OperationJournal
from hwp_operation_journal_maintenance import maintain_operation_journal
from hwp_public_document_tools import HwpPublicDocumentTools
from hwp_public_graph_tools import (
    HwpPublicGraphTools,
    PublicGraphClient,
    PublicGraphPatchHost,
)
from hwp_public_inspection_tools import HwpPublicInspectionTools
from hwp_public_live_edit_tools import HwpPublicLiveEditTools
from hwp_public_action_contract import PublicObjectTargetStore
from hwp_public_object_tools import HwpPublicObjectTools
from hwp_public_restructure_tools import HwpPublicRestructureTools
from hwp_public_selection_tools import HwpPublicSelectionTools
from hwp_public_xlsx_tools import HwpPublicXlsxTools
from hwp_public_table_edit_tools import HwpPublicTableEditTools
from hwp_public_table_target import PublicTableTargetStore
from hwp_public_table_tools import HwpPublicTableTools
from hwp_public_tools import HwpPublicTools
from hwp_public_visibility_tools import HwpPublicVisibilityTools
from hwp_reference_image_tools import HwpReferenceImageTools
from hwp_runtime_identity import startup_runtime_record


def build_server(
    controller: LiveHwpController,
    *,
    profile: McpProfile = "production",
    operation_journal: OperationJournal | None = None,
    controller_factory: Callable[[], LiveHwpController] | None = None,
    graph_client: PublicGraphClient | None = None,
    graph_artifact_root: Path | None = None,
    graph_patch_host: PublicGraphPatchHost | None = None,
    custom_action_root: Path | None = None,
) -> ForwardingFastMCP:
    bridge = HancomBridge(
        controller,
        controller_factory=controller_factory,
    )
    dispatcher = McpThreadDispatcher()
    journal = OperationJournal() if operation_journal is None else operation_journal
    operation_executor = HwpOperationExecutor(bridge, dispatcher, journal)
    operation = McpOperationHandler(bridge, dispatcher, operation_executor)
    public_targets = PublicTableTargetStore()
    object_targets = PublicObjectTargetStore()
    public_tools = HwpPublicTools(operation_executor, public_targets)
    public_table_tools = HwpPublicTableTools(operation_executor, public_targets)
    public_visibility_tools = HwpPublicVisibilityTools(operation_executor)
    public_table_edit_tools = HwpPublicTableEditTools(
        operation_executor, public_targets
    )
    public_object_tools = HwpPublicObjectTools(operation_executor, object_targets)
    public_restructure_tools = HwpPublicRestructureTools(operation_executor)
    public_inspection_tools = HwpPublicInspectionTools(
        operation_executor,
        object_targets,
        public_targets,
    )
    public_graph_tools = HwpPublicGraphTools(
        graph_client,
        graph_artifact_root
        if graph_artifact_root is not None
        else Path.home() / ".gsg-hwp" / "graph-artifacts",
        patch_host=graph_patch_host,
    )
    public_selection_tools = HwpPublicSelectionTools(operation_executor)
    public_xlsx_tools = HwpPublicXlsxTools(
        public_selection_tools.hwp_patch_text_batch
    )
    public_document_tools = HwpPublicDocumentTools(operation_executor)
    public_live_edit_tools = HwpPublicLiveEditTools(operation_executor)
    reference_image_tools = HwpReferenceImageTools()
    pageplan_tools = HwpPagePlanTools(operation_executor)
    custom_action_tools = HwpCustomActionTools(
        profile=profile,
        root=custom_action_root,
    )
    document_wrappers = McpDocumentRecipeWrappers(operation)
    qa = McpQaHandlers(bridge, dispatcher)

    def dispatch_custom_action_step(
        tool_name: str, arguments: Mapping[str, JsonValue]
    ) -> Awaitable[JsonValue]:
        """리본 버튼 클릭이 도구에 닿는 유일한 문.

        `hwp_execute`가 쓰는 바로 그 전달 경로다. 클릭이 별도 실행기를 타면 세션
        스코프·인자 정규화·검증 델리게이트를 건너뛰게 된다.
        """
        return server.call_unconverted_tool(
            tool_name, HwpExecuteArguments(root=dict(arguments))
        )

    def build_click_runtime() -> CustomActionClickRuntime | None:
        if not RIBBON_TAB_ENABLED:
            return None
        try:
            return custom_action_tools.build_runtime(dispatch_custom_action_step)
        except OSError:
            # 레지스트리 루트를 못 만들면 클릭 루프만 포기한다. 이것 때문에 워커가
            # 못 뜨면 도구 전체를 잃는다.
            return None

    @asynccontextmanager
    async def lifespan(_: FastMCP[None]) -> AsyncGenerator[None]:
        public_graph_tools.start()
        try:
            async with maintain_operation_journal(journal):
                async with maintain_live_previews(controller.preview_store):
                    try:
                        # 클릭 루프가 dispatcher보다 먼저 접힌다 — 실행 중인 스텝이
                        # 사라진 dispatcher를 만지지 않게.
                        async with maintain_custom_action_clicks(build_click_runtime()):
                            yield
                    finally:
                        await dispatcher.close(bridge.close)
        finally:
            public_graph_tools.close()
            custom_action_tools.close()

    server = ForwardingFastMCP(
        "hancom-hwp-live" if profile == "production" else "hancom-hwp-qa",
        instructions=MCP_INSTRUCTIONS,
        lifespan=lifespan,
    )
    register_mcp_tools(
        server,
        McpToolBindings(
            operation_executor=operation_executor,
            operation=operation,
            document_wrappers=document_wrappers,
            qa=qa,
            public_tools=public_tools,
            public_table_tools=public_table_tools,
            public_visibility_tools=public_visibility_tools,
            public_table_edit_tools=public_table_edit_tools,
            public_object_tools=public_object_tools,
            public_restructure_tools=public_restructure_tools,
            public_inspection_tools=public_inspection_tools,
            public_graph_tools=public_graph_tools,
            public_selection_tools=public_selection_tools,
            public_xlsx_tools=public_xlsx_tools,
            public_document_tools=public_document_tools,
            public_live_edit_tools=public_live_edit_tools,
            reference_image_tools=reference_image_tools,
            pageplan_tools=pageplan_tools,
            custom_action_tools=custom_action_tools,
        ),
        profile,
    )
    register_guidance_resources(server)
    _ = server.resource(
        "hwp-graph://artifact/{binding}/{version}/{digest}",
        name="hwp-graph-artifact",
        title="Content-addressed HWP graph artifact",
        description=(
            "Digest-verified immutable graph frame, record, property, text, or asset bytes."
        ),
        mime_type="application/octet-stream",
    )(public_graph_tools.read_artifact_resource)
    return server


# 한컴MCP 리본 탭 스위치. False 면 클릭 실행기를 세우지 않고, 그러면
# `maintain_custom_action_clicks` 가 아무 일도 하지 않아 워커가 한/글에 붙어도
# 탭이 서지 않는다 -- 그 함수의 docstring 이 이미 "리본 클릭은 부가 기능이고
# 도구는 그것 없이도 돌아야 한다"고 적어 둔 경로다.
#
# 지금 꺼져 있는 이유는 결함이 아니라 설계를 다시 하기 위해서다. 이 기능을
# 나중에 따로 다시 만들기로 했고, 그때까지 반쯤 지어진 탭이 리본에 서 있으면
# 안 된다. 도구 7종·파이썬 모듈·C++ 슬롯 풀은 그대로 둔다 -- 지우면 공개
# 스키마가 바뀌어 봉인 오라클 reseal 과 그래프 권위 재주조가 따라오고, 다시
# 만들 때 그 자산을 처음부터 다시 지어야 한다.
#
# 되살리려면 이 값을 True 로 되돌리면 된다. 이미 등록된 custom action 정의는
# 지우지 않았으므로 그대로 살아난다.
RIBBON_TAB_ENABLED: Final = False


def main() -> None:
    _ = stderr.write(f"{startup_codex_skill_registration_record()}\n")
    _ = stderr.write(f"{startup_runtime_record()}\n")
    _ = stderr.flush()
    _ = ensure_native_bridge_registered()
    # 그래프 도구는 감사 경로다. 여기서 클라이언트를 붙이지 않으면 도구 다섯이
    # GRAPH_UNAVAILABLE 만 답하고, 붙여야 세대 재사용 판정이
    # bind_graph_query_content_freshness -- 시그니처(6.2s/12.9s)가 아니라 변경
    # 토큰(20.6ms/60.3ms) -- 를 타게 된다. 창 핸들은 서버를 세울 때 아직 없으므로
    # 질의마다 연결된 문서에서 푼다.
    build_server(
        LiveHwpController(),
        profile=configured_mcp_profile(argv[1:]),
        controller_factory=LiveHwpController,
        graph_client=connected_native_graph_client(
            NativeGraphCache(Path.home() / ".gsg-hwp" / "graph-cache")
        ),
    ).run(transport="stdio")


if __name__ == "__main__":
    main()
