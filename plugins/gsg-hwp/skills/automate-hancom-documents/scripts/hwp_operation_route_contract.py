from __future__ import annotations

from typing import Literal

from pydantic import Field

from hwp_live_values import ContractModel


HwpWorkflowId = Literal[
    "document.inspect_structure",
    "document.navigate",
    "document.append_layout",
    "document.insert_layout",
    "document.rebuild",
    "document.save_reopen_verify",
    "document.replace_selection",
    "document.page_break",
    "document.insert_page",
    "document.delete_page",
    "document.undo",
    "document.redo",
    "control.delete",
    "text.insert",
    "text.replace",
    "text.format",
    "table.inspect",
    "table.create",
    "table.fill_existing",
    "table.expand_and_fill",
    "table.format",
    "table.resize",
    "table.merge_cells",
    "table.split_cells",
    "table.repeat_template",
    "table.propagate",
    "table.import_data",
    "table.insert_images",
    "table.build_series",
    "image.insert",
    "image.replace",
    "image.resize",
    "caption.add",
    "style.apply",
    "style.copy",
    "hyperlink.modify",
]
OperationRouteSource = Literal[
    "canonical_operation_id",
    "exact_alias",
    "deterministic_corpus",
    "semantic",
]


class OperationRouteMetadata(ContractModel):
    route_source: OperationRouteSource | None = None
    auto_selected: bool = False
    selected_operation: str | None = Field(default=None, min_length=1, max_length=300)
