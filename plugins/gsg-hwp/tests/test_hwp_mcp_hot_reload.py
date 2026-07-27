from __future__ import annotations

import sys
from pathlib import Path

import anyio
import mcp.types as types
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.shared.session import RequestResponder
from pydantic import BaseModel


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_mcp_hot_reload import build_proxy  # noqa: E402
from hwp_mcp_worker_session import HwpWorkerLaunch  # noqa: E402
from hwp_runtime_identity import RuntimeStatus, tool_schema_hash  # noqa: E402


class _ForwardedRuntimeStatus(BaseModel):
    result: RuntimeStatus


class _CatalogRecord(BaseModel):
    count: int
    schema_hash: str
    names: tuple[str, ...]


class _CompatibilityManifest(BaseModel):
    tool_catalogs: dict[str, _CatalogRecord]


def _worker_script(tmp_path: Path) -> Path:
    path = tmp_path / "test_hwp_worker.py"
    _ = path.write_text(
        "\n".join(
            (
                "from __future__ import annotations",
                "import sys",
                f"sys.path.insert(0, {str(SCRIPTS)!r})",
                "from hwp_live_session import LiveHwpController",
                "from hwp_mcp import build_server",
                "build_server(LiveHwpController(), profile='production').run(transport='stdio')",
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


def _runtime_status(result: types.CallToolResult) -> RuntimeStatus:
    assert result.structuredContent is not None
    return RuntimeStatus.model_validate(result.structuredContent)


def test_proxy_refreshes_worker_and_tool_catalog_without_new_client_session(
    tmp_path: Path,
) -> None:
    watched_source = tmp_path / "runtime-source.py"
    _ = watched_source.write_text("VERSION = 1\n", encoding="utf-8")
    manifest = _CompatibilityManifest.model_validate_json(
        (SCRIPTS.parents[2] / "compatibility-manifest.json").read_text(encoding="utf-8")
    )
    host_catalog = manifest.tool_catalogs["host_visible_tools"]
    proxy = build_proxy(
        HwpWorkerLaunch(
            python_executable=Path(sys.executable),
            worker_script=_worker_script(tmp_path),
            watch_paths=(watched_source,),
        )
    )
    tool_capability = proxy.initialization_options().capabilities.tools
    assert tool_capability is not None
    assert tool_capability.listChanged is True
    notifications: list[types.ServerNotification] = []

    async def capture_notification(
        message: RequestResponder[types.ServerRequest, types.ClientResult]
        | types.ServerNotification
        | Exception,
    ) -> None:
        if isinstance(message, types.ServerNotification):
            notifications.append(message)

    async def exercise_proxy() -> None:
        async with create_connected_server_and_client_session(
            proxy,
            message_handler=capture_notification,
        ) as client:
            first_tools = await client.list_tools()
            first = _runtime_status(await client.call_tool("hwp_runtime_info", {}))

            _ = watched_source.write_text("VERSION = 2\n", encoding="utf-8")
            changed = _runtime_status(await client.call_tool("hwp_runtime_info", {}))
            refreshed = _runtime_status(await client.call_tool("hwp_reload", {}))
            refreshed_tools = await client.list_tools()
            forced = _runtime_status(await client.call_tool("hwp_reload", {}))
            gateway_reload_result = await client.call_tool(
                "hwp_execute",
                {"tool_name": "hwp_reload", "arguments": {}},
            )
            assert gateway_reload_result.structuredContent is not None
            gateway_forced = _ForwardedRuntimeStatus.model_validate(
                gateway_reload_result.structuredContent
            ).result
            gateway_result = await client.call_tool(
                "hwp_execute",
                {"tool_name": "hwp_runtime_info", "arguments": {}},
            )
            assert gateway_result.structuredContent is not None
            gateway = _ForwardedRuntimeStatus.model_validate(
                gateway_result.structuredContent
            ).result

        names = {tool.name for tool in first_tools.tools}
        assert {"hwp_runtime_info", "hwp_reload", "hwp_execute"} <= names
        assert tuple(tool.name for tool in first_tools.tools) == host_catalog.names
        assert len(first_tools.tools) == host_catalog.count
        assert tool_schema_hash(first_tools.tools) == host_catalog.schema_hash
        assert sum(tool.name == "hwp_reload" for tool in first_tools.tools) == 1
        assert first.worker_state == "idle"
        assert first.reload_state == "not_requested"
        assert changed.worker_state == "idle"
        assert changed.reload_required is True
        assert changed.source_hash != changed.loaded_source_hash
        assert forced.worker_state == "idle"
        assert forced.reload_state == "reloaded"
        assert first.process_id == changed.process_id == refreshed.process_id
        assert refreshed.process_id == forced.process_id
        assert first.worker_process_id == changed.worker_process_id
        assert first.generation == changed.generation
        assert first.worker_process_id != refreshed.worker_process_id
        assert refreshed.worker_process_id != forced.worker_process_id
        assert refreshed.generation == first.generation + 1
        assert forced.generation == refreshed.generation + 1
        assert gateway_forced.worker_process_id != forced.worker_process_id
        assert gateway_forced.generation == forced.generation + 1
        assert gateway == gateway_forced
        assert refreshed.tool_schema_hash == tool_schema_hash(refreshed_tools.tools)
        assert first.python_executable == str(Path(sys.executable).resolve())
        assert (
            sum(
                isinstance(item.root, types.ToolListChangedNotification)
                for item in notifications
            )
            >= 3
        )

    anyio.run(exercise_proxy)
