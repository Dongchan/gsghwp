from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import ClassVar, Final, Literal

from mcp.types import Tool
from pydantic import BaseModel, ConfigDict, Field


class RuntimeBuildInfo(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    distribution: str = Field(min_length=1)
    mcp: str = Field(min_length=1)
    native_bridge: str = Field(min_length=1)
    protocol: Literal[9]


class RuntimeIdentity(RuntimeBuildInfo):
    recipe_bundle_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RuntimeStatus(RuntimeIdentity):
    process_id: int = Field(ge=1)
    worker_process_id: int = Field(ge=1)
    source_path: str = Field(min_length=1)
    python_executable: str = Field(min_length=1)
    tool_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_count: int = Field(ge=1)
    generation: int = Field(ge=1)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    loaded_source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reload_required: bool


class RuntimeStartupRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    event: Literal["hwp_mcp_start"] = "hwp_mcp_start"
    runtime: RuntimeIdentity


PLUGIN_ROOT: Final = Path(__file__).resolve().parents[3]
RUNTIME_WATCH_PATHS: Final = (
    Path(__file__).resolve().parent,
    PLUGIN_ROOT / "compatibility-manifest.json",
)
RUNTIME_IDENTITY: Final = RuntimeIdentity.model_validate_json(
    (PLUGIN_ROOT / "compatibility-manifest.json").read_text(
        encoding="utf-8"
    )
)
RUNTIME_BUILD_INFO: Final = RuntimeBuildInfo(
    distribution=RUNTIME_IDENTITY.distribution,
    mcp=RUNTIME_IDENTITY.mcp,
    native_bridge=RUNTIME_IDENTITY.native_bridge,
    protocol=RUNTIME_IDENTITY.protocol,
)


@dataclass(frozen=True, slots=True)
class _RuntimeSourceFile:
    path: Path
    identity: bytes


@dataclass(frozen=True, slots=True)
class RuntimeSourceState:
    directories: tuple[Path, ...]
    files: tuple[_RuntimeSourceFile, ...]
    fingerprint: tuple[tuple[int, int, int], ...]
    content_hash: str


def _runtime_source_inventory(
    paths: Sequence[Path],
) -> tuple[tuple[Path, ...], tuple[_RuntimeSourceFile, ...]]:
    directories: list[Path] = []
    files: list[_RuntimeSourceFile] = []
    for root_index, root in enumerate(paths):
        if root.is_dir():
            directories.extend(path for path in root.rglob("*") if path.is_dir())
            directories.append(root)
            source_files = tuple(
                path
                for path in sorted(root.rglob("*"))
                if path.is_file() and path.suffix in {".json", ".py"}
            )
        else:
            source_files = (root,)
        for path in source_files:
            relative = path.relative_to(root) if root.is_dir() else Path(path.name)
            files.append(
                _RuntimeSourceFile(
                    path=path,
                    identity=f"{root_index}:{relative.as_posix()}".encode("utf-8"),
                )
            )
    return tuple(sorted(set(directories))), tuple(files)


def _runtime_source_fingerprint(
    directories: Sequence[Path],
    files: Sequence[_RuntimeSourceFile],
) -> tuple[tuple[int, int, int], ...]:
    paths = (*directories, *(source.path for source in files))
    return tuple(
        (status.st_size, status.st_mtime_ns, status.st_ctime_ns)
        for status in (path.stat() for path in paths)
    )


def _runtime_content_hash(files: Sequence[_RuntimeSourceFile]) -> str:
    digest = hashlib.sha256()
    for source_file in files:
        digest.update(len(source_file.identity).to_bytes(4, "big"))
        digest.update(source_file.identity)
        if source_file.path.is_file():
            with source_file.path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
        else:
            digest.update(b"missing")
    return digest.hexdigest()


def runtime_source_state(paths: Sequence[Path]) -> RuntimeSourceState:
    directories, files = _runtime_source_inventory(paths)
    return RuntimeSourceState(
        directories=directories,
        files=files,
        fingerprint=_runtime_source_fingerprint(directories, files),
        content_hash=_runtime_content_hash(files),
    )


def refresh_runtime_source_state(
    state: RuntimeSourceState,
    paths: Sequence[Path],
) -> RuntimeSourceState:
    try:
        current_fingerprint = _runtime_source_fingerprint(
            state.directories,
            state.files,
        )
    except OSError:
        return runtime_source_state(paths)
    if current_fingerprint == state.fingerprint:
        return state
    return runtime_source_state(paths)


def runtime_source_hash(paths: Sequence[Path]) -> str:
    return runtime_source_state(paths).content_hash


RUNTIME_LOADED_SOURCE_HASH: Final = runtime_source_hash(RUNTIME_WATCH_PATHS)


def tool_schema_hash(tools: Sequence[Tool]) -> str:
    payload = [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        for tool in sorted(tools, key=lambda value: value.name)
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def runtime_status(tools: Sequence[Tool]) -> RuntimeStatus:
    current_source_hash = runtime_source_hash(RUNTIME_WATCH_PATHS)
    process_id = os.getpid()
    return RuntimeStatus(
        distribution=RUNTIME_IDENTITY.distribution,
        mcp=RUNTIME_IDENTITY.mcp,
        native_bridge=RUNTIME_IDENTITY.native_bridge,
        protocol=RUNTIME_IDENTITY.protocol,
        recipe_bundle_hash=RUNTIME_IDENTITY.recipe_bundle_hash,
        process_id=process_id,
        worker_process_id=process_id,
        source_path=str(PLUGIN_ROOT),
        python_executable=str(Path(sys.executable).resolve()),
        tool_schema_hash=tool_schema_hash(tools),
        tool_count=len(tools),
        generation=1,
        source_hash=current_source_hash,
        loaded_source_hash=RUNTIME_LOADED_SOURCE_HASH,
        reload_required=current_source_hash != RUNTIME_LOADED_SOURCE_HASH,
    )


def startup_runtime_record() -> str:
    return RuntimeStartupRecord(runtime=RUNTIME_IDENTITY).model_dump_json()
