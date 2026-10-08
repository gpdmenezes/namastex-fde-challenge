
"""Validated, normalized lead data. No business eligibility rules here."""

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LeadProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plano_id: str | None = None
    idade: int | None = Field(default=None, ge=0, le=200, strict=True)
    veiculo_ano: int | None = Field(default=None, ge=1950, le=2100, strict=True)
    cep: str | None = None
    data_inicio: date | None = None

    @field_validator("plano_id")
    @classmethod
    def normalize_plan(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if not value:
            raise ValueError("plano_id cannot be empty")
        return value

    @field_validator("cep")
    @classmethod
    def normalize_cep(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{5}-?\d{3}", value
        ):
            raise ValueError(
                "cep must have 8 digits, optionally with a hyphen"
            )
        return value.replace("-", "")


class LeadPatch(LeadProfile):
    """None means no update; explicit retraction uses clear_fields."""

    clear_fields: tuple[
        Literal[
            "plano_id",
            "idade",
            "veiculo_ano",
            "cep",
            "data_inicio",
        ],
        ...,
    ] = ()


def merge_lead(
    current: LeadProfile,
    patch: LeadPatch,
) -> tuple[LeadProfile, frozenset[str]]:
    changes = patch.model_dump(
        exclude_none=True,
        exclude={"clear_fields"},
    )

    for field in patch.clear_fields:
        if field in changes:
            raise ValueError(
                f"Cannot update and clear {field} together"
            )
        changes[field] = None

    merged = LeadProfile.model_validate({
        **current.model_dump(),
        **changes,
    })

    changed_fields = frozenset(
        field
        for field in changes
        if getattr(current, field) != getattr(merged, field)
    )

    return merged, changed_fields
