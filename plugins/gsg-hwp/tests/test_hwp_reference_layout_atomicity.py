from __future__ import annotations

from pathlib import Path


NATIVE_ROOT = Path(__file__).resolve().parents[1] / "addon" / "HancomLiveBridgeNative"


def _function(source: str, start: str, end: str) -> str:
    return source[source.index(start) : source.index(end, source.index(start))]


def test_merged_reference_text_range_starts_at_current_cell_caret() -> None:
    source = (NATIVE_ROOT / "ActionReferenceLayout.cpp").read_text(encoding="utf-8")
    function = _function(
        source,
        "bool InsertReferenceText(",
        "bool ResizeReferenceAxis(",
    )
    compact = "".join(function.split())

    assert "Positionstart;" in compact
    assert "GetPosition(context->hwp,&start,context->result)" in compact
    assert "SelectTextRange(context,start,end,location)" in compact
    assert "before.start" not in function


def test_reference_merges_refresh_span_topology_between_operations() -> None:
    source = (NATIVE_ROOT / "ActionReferenceLayout.cpp").read_text(encoding="utf-8")
    function = _function(
        source,
        "    bool MergeCells(const hancom::reference_layout::Merge& merge) override {",
        "    bool ReconcileFinalGeometry(",
    )
    compact = "".join(function.split())

    assert "context_->topology.Empty()&&!BuildCellTopology(context_)" in compact
    assert "MergeCellsUsingTopology(context_,first,last,true)" in compact


def test_document_end_reference_layout_uses_native_append_tail_rollback() -> None:
    executor = (NATIVE_ROOT / "ActionExecutor.cpp").read_text(encoding="utf-8")
    smoke = (NATIVE_ROOT / "smoke" / "BridgeSmoke.cpp").read_text(encoding="utf-8")
    compact = "".join(executor.split())

    assert "RequiresReferenceLayoutAppendRollback" in executor
    assert "request.commands.front().kind!=CommandKind::MoveDocumentEnd" in compact
    assert 'command.name==L"ReferenceLayoutBulk"' in compact
    assert (
        "constboolrollbackAppendTail="
        + "request.atomic||RequiresReferenceLayoutAppendRollback(request)"
        in compact
    )
    assert "referenceLayoutAtomicRollbackPayload" in smoke
    assert "ReferenceLayoutAtomicRollbackSucceeded" in smoke
