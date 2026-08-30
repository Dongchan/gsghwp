from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import Field, JsonValue, field_validator

from hwp_live_values import ContractModel

type ConventionState = Literal[
    "observed",
    "observed-but-conflicting",
    "unobserved",
]


class ConventionVariant(ContractModel):
    value: JsonValue
    samples: int = Field(ge=1)
    ratio: float = Field(ge=0, le=1)
    sample_paragraphs: tuple[int, ...] = Field(default=(), max_length=12)


class ConventionAxis(ContractModel):
    name: str = Field(min_length=1, max_length=80)
    state: ConventionState
    scope: str | None = Field(default=None, max_length=80)
    complete: bool = True
    sample_count: int = Field(ge=0)
    consistency: float | None = Field(default=None, ge=0, le=1)
    selected_value: JsonValue | None = None
    variants: tuple[ConventionVariant, ...] = ()
    unobserved_reason: str | None = Field(default=None, max_length=300)
    write_fields: tuple[str, ...] = ()


class ConventionSection(ContractModel):
    axes: tuple[ConventionAxis, ...]


type ConventionCacheCurrentness = Literal[
    "live_observation",
    "historical_snapshot",
]

type ConventionCacheMissReason = Literal[
    "missing",
    "dirty_open_document",
    "expired",
    "cheap_identity_mismatch",
    "content_hash_mismatch",
    "invalid_cache",
    "file_unavailable",
]


class ConventionCacheMetadata(ContractModel):
    hit: bool
    created_at: datetime | None = None
    freshness_checked_by: str = Field(min_length=1, max_length=120)
    currentness: ConventionCacheCurrentness
    miss_reason: ConventionCacheMissReason | None = None

    @field_validator("created_at")
    @classmethod
    def normalize_aware_created_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("cache created_at must include a UTC offset")
        return value.astimezone(UTC)


class DocumentConventionProfile(ContractModel):
    cache: ConventionCacheMetadata | None = None
    scanned_paragraphs: int = Field(ge=0)
    counted_paragraphs: int = Field(ge=0)
    complete: bool
    list_id: int = Field(ge=0)
    observation_errors: tuple[Annotated[str, Field(max_length=300)], ...] = Field(
        default=(),
        max_length=3,
    )
    style_transitions: ConventionSection
    numbering_definitions: ConventionSection
    character_runs: ConventionSection
    section_map: ConventionSection
    object_layout: ConventionSection
    paragraph_layout: ConventionSection
    prose_style: ConventionSection
