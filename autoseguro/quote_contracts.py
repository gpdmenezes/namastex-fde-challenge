
"""Typed DTOs for the existing quote-service. No pricing logic."""

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .domain import LeadProfile


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class HealthResponse(ApiModel):
    status: str


class Plan(ApiModel):
    id: str
    nome: str
    base_mensal: Decimal
    coberturas: list[str]
    franquia: Decimal


class PlansResponse(ApiModel):
    moeda: str
    planos: list[Plan]
    regras: dict[str, Any]


class QuoteRequest(BaseModel):
    """Stricter than API: plan selection and CEP are mandatory."""

    model_config = ConfigDict(extra="forbid")

    plano_id: str
    idade: int = Field(ge=0, le=200, strict=True)
    veiculo_ano: int = Field(ge=1950, le=2100, strict=True)
    cep: str = Field(pattern=r"^\d{8}$")
    data_inicio: date | None = None

    @classmethod
    def from_lead(cls, lead: LeadProfile) -> "QuoteRequest":
        required = (
            "plano_id",
            "idade",
            "veiculo_ano",
            "cep",
        )

        missing = [
            field
            for field in required
            if getattr(lead, field) is None
        ]

        if missing:
            raise ValueError(
                f"Missing required fields: {', '.join(missing)}"
            )

        return cls.model_validate(
            lead.model_dump(exclude_none=True)
        )


class Multipliers(ApiModel):
    faixa_etaria: Decimal
    idade_veiculo: Decimal
    regiao: Decimal


class WaitingPeriod(ApiModel):
    coberturas: list[str]
    dias: int
    observacao: str


class FirstPayment(ApiModel):
    dias_no_mes: int
    dias_cobrados: int
    valor_primeiro_pagamento: Decimal


class QuoteResponse(ApiModel):
    plano_id: str
    plano_nome: str
    premio_mensal: Decimal = Field(ge=0)
    franquia: Decimal = Field(ge=0)
    coberturas: list[str]
    multiplicadores: Multipliers
    carencia: WaitingPeriod
    moeda: str
    primeiro_pagamento_pro_rata: FirstPayment | None = None


class CommercialDecline(ApiModel):
    error: Literal["cotacao_recusada"]
    motivo: str


class InvalidPayload(ApiModel):
    error: Literal["payload_invalido"]
    detalhe: str


class UpstreamUnavailable(ApiModel):
    error: Literal["upstream_unavailable"]
    message: str


class ValidationIssue(ApiModel):
    loc: list[str | int]
    type: str
    msg: str


class SchemaValidationError(ApiModel):
    detail: list[ValidationIssue]
