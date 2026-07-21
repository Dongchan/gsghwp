#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "Pillow==12.2.0",
#     "pdfplumber==0.11.9",
#     "pydantic==2.12.5",
#     "pyhwpx==1.6.6",
#     "pywin32==312",
#     "rich==14.3.2",
#     "typer==0.23.1",
# ]
# ///

# ─── How to run ───
# 1. Install uv through your organization's approved package source.
# 2. Apply data to a template:
#      uv run hwp_automation.py apply template.hwp data.json output.hwp
# 3. Build a draft from a hybrid manifest:
#      uv run hwp_automation.py assemble manifest.json output.hwp --confirm-no-template
# 4. Render an HWP for review:
#      uv run hwp_automation.py verify output.hwp review --pages 1,3
# ──────────────────

from __future__ import annotations

import sys
from collections.abc import Callable
from io import TextIOWrapper
from pathlib import Path
from types import EllipsisType
from typing import Annotated

import typer
from rich.console import Console
from typer.models import OptionInfo

from hwp_assembly import assemble_hybrid as assemble_hybrid
from hwp_runtime import (
    DocumentAutomationError as DocumentAutomationError,
    HwpRuntimeSafetyError as HwpRuntimeSafetyError,
    HwpSession as HwpSession,
    ProcessScanError as ProcessScanError,
    WindowsProcess as WindowsProcess,
    guard_hwp_runtime as guard_hwp_runtime,
    scan_hwp_processes as scan_hwp_processes,
)
from hwp_verification import verify_document
from hwp_templates import (
    ApplyPayload as ApplyPayload,
    ImageInput as ImageInput,
    ImageMode as ImageMode,
    ImageSpec as ImageSpec,
    RepeatPage as RepeatPage,
    Scalar as Scalar,
    TextInput as TextInput,
    apply_template,
    load_apply_payload as load_apply_payload,
)
from verification_contract import VerificationRequest

if isinstance(sys.stdout, TextIOWrapper):
    _ = sys.stdout.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()
__all__ = ("HwpSession",)


def _option(
    default: str | EllipsisType = ...,
    *,
    help: str | None = None,
    bounds: tuple[int, int | None] | None = None,
) -> OptionInfo:
    minimum, maximum = (None, None) if bounds is None else bounds
    return OptionInfo(
        default=default,
        param_decls=(),
        help=help,
        min=minimum,
        max=maximum,
    )


def _run_hwp_command[T](action: Callable[[], T]) -> T:
    try:
        return action()
    except DocumentAutomationError as error:
        console.print(f"[red]중단[/red]: {error}")
        raise typer.Exit(code=1) from None


@app.command("apply")
def apply_command(
    template: Path,
    data: Path,
    output: Path,
    visible: Annotated[bool, _option(help="한컴 창을 표시합니다.")] = False,
) -> None:
    """Fill a template with text, images, and repeated pages."""
    saved = _run_hwp_command(
        lambda: apply_template(template, data, output, visible=visible)
    )
    console.print(f"[green]완료[/green]: {saved}")


@app.command("assemble")
def assemble_command(
    manifest: Path,
    output: Path,
    confirm_no_template: Annotated[
        bool,
        _option(
            "--confirm-no-template",
            help="대상 템플릿이 없음을 확인하고 빈 문서에서 조립합니다.",
        ),
    ] = False,
    visible: Annotated[bool, _option(help="한컴 창을 표시합니다.")] = False,
) -> None:
    """Create a mixed HWP draft from a PDF manifest."""
    if not confirm_no_template:
        console.print(
            "[red]중단[/red]: 대상 HWP/HWPX가 있으면 apply를 사용하세요. "
            + "빈 문서 조립은 --confirm-no-template 확인이 필요합니다."
        )
        raise typer.Exit(code=2)
    saved = _run_hwp_command(
        lambda: assemble_hybrid(manifest, output, visible=visible)
    )
    console.print(f"[green]완료[/green]: {saved}")


@app.command("verify")
def verify_command(
    document: Path,
    output_dir: Path,
    pages: Annotated[
        str | None,
        _option(help="출력 문서의 1-based 쪽 선택, 예: 1-3,7. 생략하면 전체."),
    ] = None,
    dpi: Annotated[int, _option(bounds=(96, 600))] = 180,
    manifest: Annotated[
        Path | None,
        _option(help="원본 PDF 쪽 매핑과 기하 계약을 담은 manifest v2."),
    ] = None,
    expected_page_count: Annotated[
        int | None,
        _option(
            "--expected-page-count",
            help="기대하는 HWP 출력 쪽 수.",
            bounds=(1, None),
        ),
    ] = None,
) -> None:
    """Verify export, page counts, nonblank output, and optional geometry."""
    report = _run_hwp_command(
        lambda: verify_document(
            VerificationRequest(
                document=document,
                review_dir=output_dir,
                pages=pages,
                dpi=dpi,
                manifest=manifest,
                expected_page_count=expected_page_count,
            )
        )
    )
    console.print_json(report.model_dump_json())


if __name__ == "__main__":
    app()
