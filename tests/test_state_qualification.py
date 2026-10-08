
"""Tests for lead merging, qualification, and quote invalidation."""

from datetime import date

import pytest
from pydantic import ValidationError

from autoseguro.domain import LeadPatch, LeadProfile, merge_lead
from autoseguro.qualification import (
    QualificationStatus,
    qualify,
)
from autoseguro.quote_client import QuoteAttempt, ResultKind
from autoseguro.quote_contracts import (
    PlansResponse,
    QuoteRequest,
    QuoteResponse,
)
from autoseguro.state import (
    QuoteStatus,
    apply_lead_patch,
    new_conversation_state,
    qualification_node,
)


@pytest.fixture
def catalog():
    return PlansResponse.model_validate({
        "moeda": "BRL",
        "planos": [{
            "id": "completo",
            "nome": "Completo",
            "base_mensal": "209.90",
            "coberturas": ["colisao", "roubo"],
            "franquia": 3000,
        }],
        "regras": {},
    })


@pytest.fixture
def lead():
    return LeadProfile(
        plano_id="completo",
        idade=35,
        veiculo_ano=2022,
        cep="01310-100",
    )


def test_merge_preserves_existing_values():
    original = LeadProfile(idade=35)

    merged, changed = merge_lead(
        original, LeadPatch(cep="01310-100")
    )

    assert merged.idade == 35
    assert merged.cep == "01310100"
    assert changed == frozenset({"cep"})
    assert original.cep is None


def test_merge_normalizes_and_detects_actual_changes(lead):
    merged, changed = merge_lead(
        lead,
        LeadPatch(plano_id="COMPLETO", cep="01310100"),
    )

    assert merged == lead
    assert changed == frozenset()


def test_explicit_retraction():
    original = LeadProfile(idade=35, cep="01310100")

    merged, changed = merge_lead(
        original,
        LeadPatch(clear_fields=("idade",)),
    )

    assert merged.idade is None
    assert merged.cep == "01310100"
    assert changed == frozenset({"idade"})


def test_cannot_update_and_clear_same_field():
    with pytest.raises(ValueError):
        merge_lead(
            LeadProfile(idade=35),
            LeadPatch(
                idade=36,
                clear_fields=("idade",),
            ),
        )


@pytest.mark.parametrize("invalid", [
    "ABC",
    "12345",
    "01310-1000",
    "INVALIDO",
])
def test_invalid_cep_is_rejected(invalid):
    with pytest.raises(ValidationError):
        LeadProfile(cep=invalid)


def test_missing_information_is_not_commercial_decline(catalog):
    result = qualify(LeadProfile(idade=17), catalog)

    assert result.status == QualificationStatus.NEEDS_INPUT
    assert "cep" in result.missing_fields
    assert result.invalid_fields == ()


def test_valid_data_is_ready_even_when_api_may_decline(lead, catalog):
    young = lead.model_copy(update={"idade": 17})

    result = qualify(young, catalog)

    assert result.status == QualificationStatus.READY
    assert result.missing_fields == ()


def test_unknown_plan_requires_correction(lead, catalog):
    unknown = lead.model_copy(update={"plano_id": "unknown"})

    result = qualify(unknown, catalog)

    assert result.status == QualificationStatus.NEEDS_INPUT
    assert result.invalid_fields == ("plano_id",)


def test_catalog_unavailable_is_distinct_from_missing_data(lead):
    result = qualify(lead, None)

    assert result.status == QualificationStatus.CATALOG_UNAVAILABLE
    assert result.missing_fields == ()


def test_qualification_node_does_not_mutate_state(lead, catalog):
    state = new_conversation_state()
    state["lead"] = lead
    state["catalog"] = catalog

    update = qualification_node(state)

    assert update["qualification"].status == QualificationStatus.READY
    assert state["qualification"] is None


@pytest.fixture
def quoted_state(lead, catalog):
    state = new_conversation_state()

    state["lead"] = lead
    state["catalog"] = catalog
    state["qualification"] = qualify(lead, catalog)
    state["quote_request"] = QuoteRequest.from_lead(lead)
    state["quote_id"] = "original-quote"
    state["quote_status"] = QuoteStatus.SUCCEEDED

    state["quote_response"] = QuoteResponse.model_validate({
        "plano_id": "completo",
        "plano_nome": "Completo",
        "premio_mensal": "209.90",
        "franquia": 3000,
        "coberturas": ["colisao", "roubo"],
        "multiplicadores": {
            "faixa_etaria": 1,
            "idade_veiculo": 1,
            "regiao": 1,
        },
        "carencia": {
            "coberturas": ["roubo"],
            "dias": 30,
            "observacao": "Carencia aplicavel",
        },
        "moeda": "BRL",
    })

    state["quote_attempts"] = (
        QuoteAttempt(
            number=1,
            http_status=200,
            elapsed_ms=45,
            kind=ResultKind.SUCCESS,
        ),
    )

    return state


@pytest.mark.parametrize("patch", [
    LeadPatch(plano_id="premium"),
    LeadPatch(idade=36),
    LeadPatch(veiculo_ano=2021),
    LeadPatch(cep="07000-000"),
    LeadPatch(data_inicio=date(2026, 11, 15)),
    LeadPatch(clear_fields=("idade",)),
])
def test_relevant_change_invalidates_quote(quoted_state, patch):
    original = quoted_state.copy()
    update = apply_lead_patch(quoted_state, patch)

    assert update["quote_status"] == QuoteStatus.STALE
    assert update["quote_response"] is None
    assert update["quote_request"] is None
    assert update["quote_id"] is None
    assert update["quote_attempts"] == ()
    assert update["qualification"] is None

    # The function must not mutate the input state.
    assert quoted_state == original
    assert quoted_state["quote_id"] == "original-quote"


def test_no_change_preserves_quote(quoted_state):
    update = apply_lead_patch(
        quoted_state,
        LeadPatch(cep="01310100"),
    )

    assert update == {}
    assert quoted_state["quote_status"] == QuoteStatus.SUCCEEDED
    assert quoted_state["quote_response"] is not None


def test_retraction_invalidates_and_requires_input(quoted_state):
    update = apply_lead_patch(
        quoted_state,
        LeadPatch(clear_fields=("idade",)),
    )

    new_state = {**quoted_state, **update}
    result = qualify(new_state["lead"], new_state["catalog"])

    assert new_state["quote_status"] == QuoteStatus.STALE
    assert new_state["quote_response"] is None
    assert result.status == QualificationStatus.NEEDS_INPUT
    assert "idade" in result.missing_fields
