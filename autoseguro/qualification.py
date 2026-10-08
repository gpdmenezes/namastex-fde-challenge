
"""Pure product qualification, independent of LLM and quote API."""

from enum import StrEnum

from pydantic import BaseModel

from .domain import LeadProfile
from .quote_contracts import PlansResponse


class QualificationStatus(StrEnum):
    NEEDS_INPUT = "needs_input"
    CATALOG_UNAVAILABLE = "catalog_unavailable"
    READY = "ready"


class QualificationResult(BaseModel):
    status: QualificationStatus
    missing_fields: tuple[str, ...] = ()
    invalid_fields: tuple[str, ...] = ()


def qualify(
    lead: LeadProfile,
    catalog: PlansResponse | None,
) -> QualificationResult:
    required = (
        "plano_id",
        "idade",
        "veiculo_ano",
        "cep",
    )

    missing = tuple(
        key
        for key in required
        if getattr(lead, key) is None
    )

    invalid: tuple[str, ...] = ()

    if catalog is not None and lead.plano_id is not None:
        ids = {plan.id for plan in catalog.planos}

        if lead.plano_id not in ids:
            invalid = ("plano_id",)

    if missing or invalid:
        return QualificationResult(
            status=QualificationStatus.NEEDS_INPUT,
            missing_fields=missing,
            invalid_fields=invalid,
        )

    if catalog is None or not catalog.planos:
        return QualificationResult(
            status=QualificationStatus.CATALOG_UNAVAILABLE
        )

    return QualificationResult(
        status=QualificationStatus.READY
    )
