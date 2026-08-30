from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


Sha256 = str
DocumentRole = Literal["fast", "acceptance-once"]
Fault = Literal["none", "bad_sha", "first", "middle", "last", "post_write"]


class StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class Artifact(StrictModel):
    path: Path
    bytes: int = Field(gt=0)
    sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")


class PptxSource(Artifact):
    slide: int = Field(gt=0)


class XlsSource(Artifact):
    sheet: str = Field(min_length=1)
    cell_range: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*:[A-Z]+[1-9][0-9]*$")


class Sources(StrictModel):
    pptx: PptxSource
    xls: XlsSource


class Document(Artifact):
    name: str = Field(min_length=1)
    role: DocumentRole


class ProcessPolicy(StrictModel):
    protected_pid_baseline: tuple[int, ...]
    owned_pid_baseline: tuple[int, ...]
    preserve_preexisting_hwp_processes: bool
    preserve_preexisting_mcp_processes: bool
    terminate_owned_pids_only: bool
    cleanup_order: tuple[Literal["Clear(1)", "Quit()"], ...]


class SelectorPolicy(StrictModel):
    obtain_via: Literal["hwp_list_open_documents"]
    obtain_once: Literal[True]
    use_exact_returned_selector_on_every_live_call: Literal[True]
    use_exact_returned_document_id_on_every_live_call: Literal[True]
    require_exact_process_id: bool
    require_exact_window_handle: bool
    reject_active_window_fallback: Literal[True]
    require_unique_match: bool


class FreshCopyPolicy(StrictModel):
    required: bool
    preserve_source: bool
    one_copy_per_workflow: bool
    delete_copy_after_cleanup: bool
    isolated_roots: Literal[True]
    executor_root: Path
    verifier_root: Path
    naming_template: Literal["{role}_{run_id}{suffix}"]


class WaitPolicy(StrictModel):
    event_driven: bool
    bounded: bool
    timeout_seconds: int = Field(gt=0)
    fixed_sleep_allowed: bool


class UnresolvedExpectation(StrictModel):
    status: Literal["unresolved"]
    value: None = None
    reason: str = Field(min_length=1)


class Workflow(StrictModel):
    expected_final_render_count: int = Field(ge=0)
    expected_page_count: UnresolvedExpectation
    expected_control_count: UnresolvedExpectation
    expected_table_count: UnresolvedExpectation
    expected_status: Literal["success"]
    expected_error: None = None
    expected_signature: UnresolvedExpectation
    expected_hash: UnresolvedExpectation
    faults: tuple[Fault, ...]


class Workflows(StrictModel):
    g04: Workflow
    g05: Workflow


class QaScenario(StrictModel):
    version: Literal[1]
    schema_id: Literal["gsg.hwp.qa-scenario.v1"]
    scenario_hash: Sha256 | None
    sources: Sources
    documents: tuple[Document, ...]
    process_policy: ProcessPolicy
    selector_policy: SelectorPolicy
    fresh_copy_policy: FreshCopyPolicy
    stage_order: tuple[str, ...]
    workflows: Workflows
    receipt_fields: tuple[str, ...]
    undo_fields: tuple[str, ...]
    wait_policy: WaitPolicy


def canonical_scenario_hash(scenario: QaScenario) -> str:
    payload = scenario.model_dump(mode="json", exclude={"scenario_hash"})
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_scenario(path: Path) -> QaScenario:
    return QaScenario.model_validate_json(path.read_text(encoding="utf-8"))


def _verify_artifact(artifact: Artifact) -> None:
    stat = artifact.path.stat()
    if stat.st_size != artifact.bytes:
        size_mismatch_message = (
            "ARTIFACT_SIZE_MISMATCH: {}: expected={}, actual={}".format(
                artifact.path,
                artifact.bytes,
                stat.st_size,
            )
        )
        raise ValueError(size_mismatch_message)
    with artifact.path.open("rb") as stream:
        actual_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual_sha256 != artifact.sha256:
        sha256_mismatch_message = (
            "ARTIFACT_SHA256_MISMATCH: {}: expected={}, actual={}".format(
                artifact.path,
                artifact.sha256,
                actual_sha256,
            )
        )
        raise ValueError(sha256_mismatch_message)


def load_scenario(path: str | Path, *, verify_files: bool = False) -> QaScenario:
    scenario = _parse_scenario(Path(path))
    actual_hash = canonical_scenario_hash(scenario)
    if scenario.scenario_hash != actual_hash:
        scenario_hash_mismatch_message = (
            "SCENARIO_HASH_MISMATCH: expected={}, actual={}".format(
                scenario.scenario_hash,
                actual_hash,
            )
        )
        raise ValueError(scenario_hash_mismatch_message)

    if verify_files:
        _verify_artifact(scenario.sources.pptx)
        _verify_artifact(scenario.sources.xls)
        for document in scenario.documents:
            _verify_artifact(document)

    return scenario


class Arguments(argparse.Namespace):
    scenario: Path = Path()
    verify_files: bool = False
    print_canonical_hash: bool = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("scenario", type=Path)
    _ = parser.add_argument("--verify-files", action="store_true")
    _ = parser.add_argument("--print-canonical-hash", action="store_true")
    arguments = parser.parse_args(argv, namespace=Arguments())

    if arguments.print_canonical_hash:
        scenario = _parse_scenario(arguments.scenario)
        _ = print(canonical_scenario_hash(scenario))
        return 0

    scenario = load_scenario(
        arguments.scenario,
        verify_files=arguments.verify_files,
    )
    _ = print(scenario.scenario_hash)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
