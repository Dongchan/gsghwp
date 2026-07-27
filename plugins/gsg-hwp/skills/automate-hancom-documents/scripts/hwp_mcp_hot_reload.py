from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Final, Literal, assert_never, final, override

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
        tools = host_visible_tool_catalog(worker.tools).tools
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
                if result.isError:
                    raise ToolError(
                        str(result.content[0])
                        if result.content
                        else "HWP worker tool returned an error"
                    )
                if result.structuredContent is not None:
                    return _JSON_OBJECT.validate_python(result.structuredContent)
                return result.content
            case unreachable:
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
