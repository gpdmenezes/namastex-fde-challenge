
"""Deterministic tests for the LangGraph conversation workflow."""

from collections import deque
from decimal import Decimal

import pytest

from autoseguro.domain import LeadPatch
from autoseguro.quote_client import (
    QuoteAttempt,
    QuoteClientError,
    QuoteResult,
    ResultKind,
)
from autoseguro.quote_contracts import PlansResponse, QuoteResponse
from autoseguro.state import (
    HandoffReason,
    QuoteStatus,
    TurnAction,
    new_conversation_state,
)
from autoseguro.workflow import build_graph, run_turn


def make_catalog():
    return PlansResponse.model_validate({
        "moeda": "BRL",
        "planos": [
            {
                "id": "completo",
                "nome": "Completo",
                "base_mensal": "209.90",
                "coberturas": ["colisao", "roubo", "furto"],
                "franquia": 3000,
            },
            {
                "id": "premium",
                "nome": "Premium",
                "base_mensal": "339.90",
                "coberturas": ["colisao", "roubo", "furto"],
                "franquia": 1500,
            },
        ],
        "regras": {},
    })


def make_quote(price="209.90", plan="completo"):
    return QuoteResponse.model_validate({
        "plano_id": plan,
        "plano_nome": plan.capitalize(),
        "premio_mensal": price,
        "franquia": 3000,
        "coberturas": ["colisao", "roubo", "furto"],
        "multiplicadores": {
            "faixa_etaria": 1,
            "idade_veiculo": 1,
            "regiao": 1,
        },
        "carencia": {
            "coberturas": ["roubo", "furto"],
            "dias": 30,
            "observacao": "Carencia aplicavel",
        },
        "moeda": "BRL",
        "primeiro_pagamento_pro_rata": {
            "dias_no_mes": 30,
            "dias_cobrados": 16,
            "valor_primeiro_pagamento": "111.95",
        },
    })


def make_result(
    kind=ResultKind.SUCCESS,
    *,
    quote=None,
    reason=None,
    attempts=1,
    quote_id="quote-001",
):
    if kind == ResultKind.SUCCESS and quote is None:
        quote = make_quote()

    return QuoteResult(
        quote_id=quote_id,
        kind=kind,
        quote=quote,
        attempts=tuple(
            QuoteAttempt(
                number=i,
                http_status=(
                    200 if kind == ResultKind.SUCCESS else None
                ),
                elapsed_ms=10,
                kind=kind,
            )
            for i in range(1, attempts + 1)
        ),
        total_elapsed_ms=attempts * 10,
        decline_reason=reason,
    )


class FakeQuoteClient:
    def __init__(self, results=(), catalog=None, catalog_error=None):
        self.results = deque(results)
        self.catalog = catalog if catalog is not None else make_catalog()
        self.catalog_error = catalog_error
        self.catalog_calls = 0
        self.quote_requests = []

    def get_plans(self):
        self.catalog_calls += 1

        if self.catalog_error is not None:
            raise self.catalog_error

        return self.catalog

    def quote(self, request):
        self.quote_requests.append(request)

        if not self.results:
            raise AssertionError("Unexpected quote request")

        return self.results.popleft()


def complete_patch(**overrides):
    values = {
        "plano_id": "completo",
        "idade": 35,
        "veiculo_ano": 2022,
        "cep": "01310-100",
    }
    values.update(overrides)
    return LeadPatch(**values)


def execute(fake, patch=None, state=None, request_handoff=False):
    graph = build_graph(fake)
    return run_turn(
        graph,
        state or new_conversation_state(),
        patch=patch,
        request_handoff=request_handoff,
    )


def test_missing_data_does_not_quote_or_decline():
    fake = FakeQuoteClient()

    result = execute(fake, LeadPatch(idade=35))

    assert result["next_action"] == TurnAction.ASK_INPUT
    assert result["quote_status"] == QuoteStatus.NOT_REQUESTED
    assert result["handoff_reason"] is None
    assert result["quote_response"] is None
    assert "cep" in result["qualification"].missing_fields
    assert fake.quote_requests == []
    assert fake.catalog_calls == 0


def test_incremental_collection_and_quote():
    fake = FakeQuoteClient([make_result()])
    graph = build_graph(fake)

    state = new_conversation_state()

    state = run_turn(
        graph,
        state,
        patch=LeadPatch(idade=35, veiculo_ano=2022),
    )
    assert state["next_action"] == TurnAction.ASK_INPUT

    state = run_turn(
        graph,
        state,
        patch=LeadPatch(cep="01310-100"),
    )
    assert state["next_action"] == TurnAction.ASK_INPUT

    state = run_turn(
        graph,
        state,
        patch=LeadPatch(plano_id="completo"),
    )

    assert state["next_action"] == TurnAction.SHOW_QUOTE
    assert state["quote_status"] == QuoteStatus.SUCCEEDED
    assert state["quote_response"].premio_mensal == Decimal("209.90")
    assert (
        state["quote_response"]
        .primeiro_pagamento_pro_rata.valor_primeiro_pagamento
        == Decimal("111.95")
    )
    assert state["quote_request"].cep == "01310100"
    assert fake.catalog_calls == 1
    assert len(fake.quote_requests) == 1


def test_success_is_not_quoted_again_without_changes():
    fake = FakeQuoteClient([make_result()])
    graph = build_graph(fake)

    state = run_turn(
        graph, new_conversation_state(), patch=complete_patch()
    )
    state = run_turn(graph, state)

    assert state["next_action"] == TurnAction.SHOW_QUOTE
    assert state["quote_id"] == "quote-001"
    assert len(fake.quote_requests) == 1
    assert fake.catalog_calls == 1


def test_commercial_decline_never_auto_handoffs_or_requotes():
    fake = FakeQuoteClient([
        make_result(
            ResultKind.COMMERCIAL_DECLINE,
            reason="Idade fora da faixa",
        )
    ])
    graph = build_graph(fake)

    state = run_turn(
        graph, new_conversation_state(), patch=complete_patch()
    )

    assert state["next_action"] == TurnAction.EXPLAIN_DECLINE
    assert state["quote_status"] == QuoteStatus.DECLINED
    assert state["handoff_reason"] is None
    assert state["decline_reason"] == "Idade fora da faixa"
    assert state["quote_response"] is None

    state = run_turn(graph, state)

    assert state["next_action"] == TurnAction.EXPLAIN_DECLINE
    assert len(fake.quote_requests) == 1


@pytest.mark.parametrize("kind", [
    ResultKind.TRANSIENT_ERROR,
    ResultKind.TIMEOUT,
    ResultKind.TRANSPORT_ERROR,
])
def test_exhausted_technical_failure_handoffs(kind):
    fake = FakeQuoteClient([
        make_result(kind, attempts=3)
    ])

    state = execute(fake, complete_patch())

    assert state["next_action"] == TurnAction.HANDOFF
    assert state["handoff_reason"] == HandoffReason.QUOTE_UNAVAILABLE
    assert state["quote_status"] == QuoteStatus.UNAVAILABLE
    assert state["quote_error_kind"] == kind
    assert state["quote_response"] is None
    assert len(state["quote_attempts"]) == 3


@pytest.mark.parametrize("kind", [
    ResultKind.INVALID_PAYLOAD,
    ResultKind.SCHEMA_VALIDATION,
    ResultKind.INVALID_RESPONSE,
    ResultKind.UNEXPECTED_HTTP,
])
def test_contract_errors_handoff_as_integration_error(kind):
    fake = FakeQuoteClient([make_result(kind)])

    state = execute(fake, complete_patch())

    assert state["next_action"] == TurnAction.HANDOFF
    assert state["handoff_reason"] == HandoffReason.INTEGRATION_ERROR
    assert state["quote_status"] == QuoteStatus.UNAVAILABLE
    assert state["quote_response"] is None


def test_catalog_failure_handoffs_without_quote():
    fake = FakeQuoteClient(
        catalog_error=QuoteClientError(ResultKind.TRANSIENT_ERROR, 503)
    )

    state = execute(fake, complete_patch())

    assert state["next_action"] == TurnAction.HANDOFF
    assert state["handoff_reason"] == HandoffReason.CATALOG_UNAVAILABLE
    assert state["quote_status"] == QuoteStatus.NOT_REQUESTED
    assert len(fake.quote_requests) == 0
    assert fake.catalog_calls == 1


def test_empty_catalog_does_not_loop():
    empty = PlansResponse(
        moeda="BRL",
        planos=[],
        regras={},
    )
    fake = FakeQuoteClient(catalog=empty)

    state = execute(fake, complete_patch())

    assert state["next_action"] == TurnAction.HANDOFF
    assert state["handoff_reason"] == HandoffReason.CATALOG_UNAVAILABLE
    assert fake.catalog_calls == 1


def test_unknown_plan_requires_input_not_commercial_decline():
    fake = FakeQuoteClient()

    state = execute(
        fake, complete_patch(plano_id="inexistente")
    )

    assert state["next_action"] == TurnAction.ASK_INPUT
    assert state["qualification"].invalid_fields == ("plano_id",)
    assert state["quote_status"] == QuoteStatus.NOT_REQUESTED
    assert state["handoff_reason"] is None
    assert len(fake.quote_requests) == 0


def test_changed_cep_invalidates_and_requotes():
    fake = FakeQuoteClient([
        make_result(quote_id="old", quote=make_quote("209.90")),
        make_result(quote_id="new", quote=make_quote("272.87")),
    ])
    graph = build_graph(fake)

    state = run_turn(
        graph, new_conversation_state(), patch=complete_patch()
    )

    assert state["quote_id"] == "old"

    state = run_turn(
        graph,
        state,
        patch=LeadPatch(cep="07000-000"),
    )

    assert state["quote_id"] == "new"
    assert state["quote_status"] == QuoteStatus.SUCCEEDED
    assert state["quote_request"].cep == "07000000"
    assert state["quote_response"].premio_mensal == Decimal("272.87")
    assert len(fake.quote_requests) == 2
    assert fake.catalog_calls == 1


def test_same_normalized_value_does_not_requote():
    fake = FakeQuoteClient([make_result()])
    graph = build_graph(fake)

    state = run_turn(
        graph, new_conversation_state(), patch=complete_patch()
    )

    state = run_turn(
        graph, state, patch=LeadPatch(cep="01310100")
    )

    assert state["quote_id"] == "quote-001"
    assert state["quote_status"] == QuoteStatus.SUCCEEDED
    assert len(fake.quote_requests) == 1


def test_explicit_handoff_has_priority_over_qualification():
    fake = FakeQuoteClient()

    state = execute(
        fake,
        LeadPatch(idade=35),
        request_handoff=True,
    )

    assert state["next_action"] == TurnAction.HANDOFF
    assert state["handoff_reason"] == HandoffReason.USER_REQUEST
    assert fake.catalog_calls == 0
    assert len(fake.quote_requests) == 0


def test_handoff_remains_active_on_following_turn():
    fake = FakeQuoteClient([
        make_result(ResultKind.TIMEOUT, attempts=3)
    ])
    graph = build_graph(fake)

    state = run_turn(
        graph, new_conversation_state(), patch=complete_patch()
    )
    assert state["next_action"] == TurnAction.HANDOFF

    state = run_turn(
        graph, state, patch=LeadPatch(idade=36)
    )

    assert state["next_action"] == TurnAction.HANDOFF
    assert state["handoff_reason"] == HandoffReason.QUOTE_UNAVAILABLE
    assert state["quote_status"] == QuoteStatus.STALE
    assert state["quote_response"] is None
    assert len(fake.quote_requests) == 1


def test_mismatched_plan_response_is_never_presented():
    fake = FakeQuoteClient([
        make_result(quote=make_quote(plan="premium"))
    ])

    state = execute(fake, complete_patch())

    assert state["next_action"] == TurnAction.HANDOFF
    assert state["handoff_reason"] == HandoffReason.INTEGRATION_ERROR
    assert state["quote_error_kind"] == ResultKind.INVALID_RESPONSE
    assert state["quote_response"] is None
