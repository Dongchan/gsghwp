from __future__ import annotations

from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from typing_extensions import TypeIs


class NativeReady(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    type: Literal["ready"]
    protocol: Literal[1]
    moniker: str
    source_iid: str


class NativeEvent(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    type: Literal["event"]
    event: str
    dispid: int
    document_id: int | None


type NativeMessage = Annotated[NativeReady | NativeEvent, Field(discriminator="type")]
_MESSAGE_ADAPTER: Final[TypeAdapter[NativeMessage]] = TypeAdapter(NativeMessage)


def parse_native_message(line: str) -> NativeMessage:
    return _MESSAGE_ADAPTER.validate_json(line)


def is_native_message(value: NativeMessage | None) -> TypeIs[NativeMessage]:
    return isinstance(value, (NativeReady, NativeEvent))
