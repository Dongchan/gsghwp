from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


Alignment = Literal["inherit", "left", "center", "right", "justify"]
RgbChannel = Annotated[int, Field(ge=0, le=255)]
Rgb = tuple[RgbChannel, RgbChannel, RgbChannel]
