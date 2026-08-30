from __future__ import annotations

from pydantic import Field

from hwp_live_values import ContractModel


class TablePictureFact(ContractModel):
    instance_id: str = Field(max_length=100)
    cell_address: str = Field(pattern=r"^[A-Z]+[1-9][0-9]*$")


class DocumentTableFact(ContractModel):
    page: int = Field(ge=1)
    table_instance_id: str = Field(max_length=100)
    rows: int | None = Field(default=None, ge=1)
    columns: int | None = Field(default=None, ge=1)
    picture_count: int = Field(ge=0)
    pictures: tuple[TablePictureFact, ...] = ()


class TableInventoryPageError(ContractModel):
    code: str = Field(max_length=100)
    message: str = Field(max_length=2_000)
    control_instance_id: str | None = Field(default=None, max_length=100)


class TableInventoryPageFact(ContractModel):
    page: int = Field(ge=1)
    read: bool
    table_count: int = Field(ge=0)
    picture_count: int = Field(ge=0)
    errors: tuple[TableInventoryPageError, ...] = ()


class DocumentTableInventory(ContractModel):
    document_id: int
    full_name: str = Field(max_length=1_000)
    page_count: int = Field(ge=1)
    min_pictures: int = Field(ge=0)
    tables: tuple[DocumentTableFact, ...]
    pages: tuple[TableInventoryPageFact, ...]
