from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Final, Literal, assert_never, final, override

import jsonschema
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import ContentBlock, Tool
from pydantic import JsonValue, TypeAdapter

from hwp_mcp_catalog import host_visible_tool_catalog
from hwp_mcp_registry import configured_mcp_profile
from hwp_mcp_resources import register_guidance_resources
from hwp_mcp_worker_protocol import HwpWorkerCallTimeout
from hwp_mcp_worker_session import HwpWorkerLaunch
from hwp_mcp_worker_supervisor import HwpWorkerSupervisor
from hwp_runtime_identity import (
    PLUGIN_ROOT,
    RUNTIME_WATCH_PATHS,
    RuntimeStatus,
    native_bridge_runtime,
    tool_schema_hash,
)


type _ProxyLifespan = Callable[
    [FastMCP[None]],
    AbstractAsyncContextManager[None],
]

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_RUNTIME_ACTIONS: Final[Mapping[str, Literal["status", "reload"]]] = MappingProxyType(
    {
        "hwp_runtime_info": "status",
        "hwp_reload": "reload",
    }
)


@dataclass(frozen=True, slots=True)
class _RuntimeRoute:
    action: Literal["status", "reload"]
    through_gateway: bool


def _runtime_action(
    name: str,
    arguments: dict[str, JsonValue],
) -> _RuntimeRoute | None:
    direct = _RUNTIME_ACTIONS.get(name)
    if direct is not None:
        return _RuntimeRoute(direct, False)
    if name != "hwp_execute":
        return None
    forwarded = _RUNTIME_ACTIONS.get(str(arguments.get("tool_name", "")))
    return None if forwarded is None else _RuntimeRoute(forwarded, True)


@final
class ReloadingHwpMCP(FastMCP[None]):
    __slots__ = ("_supervisor",)

    def __init__(
        self,
        supervisor: HwpWorkerSupervisor,
        lifespan: _ProxyLifespan,
    ) -> None:
        self._supervisor = supervisor
        super().__init__(
            "hancom-hwp-reloading-proxy",
            lifespan=lifespan,
        )

    @override
    async def list_tools(self) -> list[Tool]:
        worker = await self._supervisor.list_tools()
        return list(host_visible_tool_catalog(worker.tools).tools)

    def initialization_options(self) -> InitializationOptions:
        return self._mcp_server.create_initialization_options(
            NotificationOptions(tools_changed=True)
        )

    @override
    async def run_stdio_async(self) -> None:
        async with stdio_server() as (read_stream, write_stream):
            await self._mcp_server.run(
                read_stream,
                write_stream,
                self.initialization_options(),
            )

    async def _runtime_status(
        self, *, reload_worker: bool
    ) -> tuple[RuntimeStatus, bool]:
        worker = (
            await self._supervisor.reload()
            if reload_worker
            else await self._supervisor.status()
        )
        activity = self._supervisor.activity_snapshot()
        tools = host_visible_tool_catalog(worker.tools).tools
        bridge = native_bridge_runtime()
        bridge_notice = bridge.native_bridge_notice
        if activity.active_tool_names:
            bridge_notice = (
                f"{bridge_notice}; "
                f"worker_busy_tools={','.join(activity.active_tool_names)}; "
                f"busy_elapsed_seconds={activity.busy_elapsed_seconds:.3f}; "
                f"busy_timeout_seconds={activity.busy_timeout_seconds:.3f}; "
                "busy_timeout_remaining_seconds="
                f"{activity.busy_timeout_remaining_seconds:.3f}; "
                "user_action=진행 중인 호출을 취소하거나 제한시간까지 기다리세요. "
                "취소 또는 제한시간 초과 시 worker가 격리·재시작됩니다. "
                "즉시 자동화 참조를 끊으려면 hwp_disconnect를 호출하세요"
            )
        # worker.runtime is a snapshot taken when the worker cycle started, so
        # its bridge reading ages the moment Hangul restarts. Re-read it here,
        # at answer time, or this tool repeats the failure it exists to expose.
        runtime = worker.runtime.model_copy(
            update={
                "process_id": os.getpid(),
                "worker_process_id": worker.runtime.process_id,
                "tool_schema_hash": tool_schema_hash(tools),
                "tool_count": len(tools),
                "generation": worker.generation,
                "source_hash": worker.source_hash,
                "loaded_source_hash": worker.loaded_source_hash,
                "reload_required": (worker.source_hash != worker.loaded_source_hash),
                "worker_state": worker.worker_state,
                "reload_state": worker.reload_state,
                **dict(bridge),
                "native_bridge_notice": bridge_notice,
            }
        )
        return runtime, worker.reloaded

    async def _notify_if_reloaded(self, reloaded: bool) -> None:
        if reloaded:
            await self.get_context().session.send_tool_list_changed()

    @override
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> Sequence[ContentBlock] | dict[str, JsonValue]:
        route = _runtime_action(name, arguments)
        match route:
            case _RuntimeRoute(action=action, through_gateway=through_gateway):
                runtime, reloaded = await self._runtime_status(
                    reload_worker=action == "reload"
                )
                await self._notify_if_reloaded(reloaded)
                payload = _JSON_OBJECT.validate_python(runtime.model_dump(mode="json"))
                return {"result": payload} if through_gateway else payload
            case None:
                try:
                    worker = await self._supervisor.call_tool(name, arguments)
                except HwpWorkerCallTimeout as error:
                    raise ToolError(str(error)) from error
                await self._notify_if_reloaded(worker.reloaded)
                result = worker.result
                failure_tool = worker.supervisor_failure_tool
                if failure_tool is not None and failure_tool.outputSchema is not None:
                    failure_text = next(
                        (
                            block.text
                            for block in result.content
                            if block.type == "text"
                        ),
                        "HWP supervisor synthesized an invalid tool result",
                    )
                    if result.structuredContent is None:
                        raise ToolError(failure_text)
                    try:
                        jsonschema.validate(
                            instance=result.structuredContent,
                            schema=failure_tool.outputSchema,
                        )
                    except jsonschema.ValidationError:
                        raise ToolError(failure_text) from None
                if result.isError:
                    raise ToolError(
                        str(result.content[0])
                        if result.content
                        else "HWP worker tool returned an error"
                    )
                if result.structuredContent is not None:
                    return _JSON_OBJECT.validate_python(result.structuredContent)
                return result.content
            case unreachable:  # pyright: ignore[reportUnnecessaryComparison]
                assert_never(unreachable)


def build_proxy(launch: HwpWorkerLaunch | None = None) -> ReloadingHwpMCP:
    resolved_launch = launch or HwpWorkerLaunch(
        python_executable=Path(sys.executable),
        worker_script=Path(__file__).with_name("hwp_mcp.py"),
        watch_paths=RUNTIME_WATCH_PATHS,
    )
    supervisor = HwpWorkerSupervisor(resolved_launch)

    @asynccontextmanager
    async def lifespan(_: FastMCP[None]) -> AsyncGenerator[None]:
        async with supervisor.running():
            yield

    server = ReloadingHwpMCP(supervisor, lifespan)
    register_guidance_resources(server)
    return server


def main() -> None:
    profile = configured_mcp_profile(sys.argv[1:])
    worker = (
        PLUGIN_ROOT / "skills" / "automate-hancom-documents" / "scripts" / "hwp_mcp.py"
    )
    build_proxy(
        HwpWorkerLaunch(
            python_executable=Path(sys.executable),
            worker_script=worker,
            watch_paths=RUNTIME_WATCH_PATHS,
            worker_arguments=("--profile", profile),
        )
    ).run(transport="stdio")


if __name__ == "__main__":
    main()
