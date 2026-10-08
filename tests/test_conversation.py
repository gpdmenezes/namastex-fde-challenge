
"""Tests for semantic extraction and multi-turn integration."""

from collections import deque
from decimal import Decimal
from types import SimpleNamespace

import pytest

from autoseguro.conversation import ConversationService
from autoseguro.domain import LeadProfile
from autoseguro.extraction import (
    ExtractionError,
    Intent,
    LeadField,
    OpenAISemanticExtractor,
    SemanticExtraction,
)
from autoseguro.quote_client import (
    QuoteAttempt,
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
from autoseguro.workflow import build_graph


def catalog():
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


def quote(price="209.90", plan="completo"):
    return QuoteResponse.model_validate({
        "plano_id": plan,
        "plano_nome": plan.capitalize(),
        "premio_mensal": price,
        "franquia": "3000",
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


def result(
    kind=ResultKind.SUCCESS,
    *,
    price="209.90",
    plan="completo",
    reason=None,
    quote_id="q-001",
):
    response = (
        quote(price, plan)
        if kind == ResultKind.SUCCESS
        else None
    )

    return QuoteResult(
        quote_id=quote_id,
        kind=kind,
        quote=response,
        attempts=(
            QuoteAttempt(
                number=1,
                http_status=200 if response else 422,
                elapsed_ms=10,
                kind=kind,
            ),
        ),
        total_elapsed_ms=10,
        decline_reason=reason,
    )


class FakeClient:
    def __init__(self, results=()):
        self.results = deque(results)
        self.quote_requests = []
        self.catalog_calls = 0

    def get_plans(self):
        self.catalog_calls += 1
        return catalog()

    def quote(self, request):
        self.quote_requests.append(request)
        if not self.results:
            raise AssertionError("Unexpected quote call")
        return self.results.popleft()


class FakeExtractor:
    def __init__(self, *outputs):
        self.outputs = deque(outputs)

    def extract(self, message, state):
        if not self.outputs:
            raise AssertionError("Unexpected extraction")
        output = self.outputs.popleft()
        if isinstance(output, Exception):
            raise output
        return output


def extraction(intent=Intent.INFORMATION, **fields):
    return SemanticExtraction(intent=intent, **fields)


def service(outputs, results=()):
    client = FakeClient(results)
    graph = build_graph(client)
    extractor = FakeExtractor(*outputs)
    return ConversationService(graph, extractor), client


def test_three_turn_quote_with_partial_information():
    agent, client = service([
        extraction(Intent.QUOTE),
        extraction(idade=35, veiculo_ano=2022),
        extraction(cep="01310-100", plano_id="completo"),
    ], [result()])

    state = new_conversation_state()

    turn = agent.handle(state, "Quero cotar um seguro")
    assert turn.state["next_action"] == TurnAction.ASK_INPUT
    assert turn.state["quote_flow_active"]
    assert len(client.quote_requests) == 0

    turn = agent.handle(
        turn.state,
        "Tenho 35 anos e meu carro é de 2022",
    )
    assert turn.state["lead"].idade == 35
    assert turn.state["lead"].veiculo_ano == 2022
    assert turn.state["next_action"] == TurnAction.ASK_INPUT

    turn = agent.handle(
        turn.state,
        "CEP 01310-100, plano completo",
    )

    assert turn.state["next_action"] == TurnAction.SHOW_QUOTE
    assert turn.state["quote_status"] == QuoteStatus.SUCCEEDED
    assert turn.state["lead"].idade == 35
    assert turn.state["lead"].veiculo_ano == 2022
    assert turn.state["lead"].cep == "01310100"
    assert turn.state["quote_response"].premio_mensal == Decimal("209.90")
    assert len(client.quote_requests) == 1
    assert client.catalog_calls == 1
    assert "R$ 209,90" in turn.text
    assert "R$ 111,95" in turn.text


def test_partial_data_misclassified_as_ambiguous_is_preserved():
    agent, client = service([
        extraction(Intent.QUOTE),
        extraction(Intent.AMBIGUOUS, idade=35),
    ])

    state = agent.handle(
        new_conversation_state(),
        "Quero seguro",
    ).state

    turn = agent.handle(state, "Tenho 35 anos")

    assert turn.state["lead"].idade == 35
    assert turn.state["next_action"] == TurnAction.ASK_INPUT
    assert "idade do motorista" not in turn.text
    assert len(client.quote_requests) == 0


def test_null_does_not_erase_previous_values():
    agent, client = service([
        extraction(Intent.QUOTE, idade=35),
        extraction(veiculo_ano=2022),
        extraction(plano_id="completo", cep="01310100"),
    ], [result()])

    state = new_conversation_state()
    state = agent.handle(state, "Quero seguro, tenho 35").state
    state = agent.handle(state, "Carro de 2022").state
    turn = agent.handle(state, "Completo, CEP 01310100")

    assert turn.state["lead"].idade == 35
    assert turn.state["next_action"] == TurnAction.SHOW_QUOTE
    assert len(client.quote_requests) == 1


def test_correction_requotes_with_new_information():
    agent, client = service([
        extraction(
            Intent.QUOTE,
            idade=35,
            veiculo_ano=2022,
            cep="01310100",
            plano_id="completo",
        ),
        extraction(veiculo_ano=2021),
    ], [
        result(price="209.90", quote_id="old"),
        result(price="241.38", quote_id="new"),
    ])

    state = agent.handle(
        new_conversation_state(),
        "Quero seguro para meu carro de 2022...",
    ).state

    assert state["quote_id"] == "old"

    turn = agent.handle(state, "Na verdade, meu carro é 2021")

    assert turn.state["quote_id"] == "new"
    assert turn.state["lead"].veiculo_ano == 2021
    assert turn.state["quote_response"].premio_mensal == Decimal("241.38")
    assert len(client.quote_requests) == 2


def test_ambiguous_correction_invalidates_old_quote():
    agent, client = service([
        extraction(
            Intent.QUOTE,
            idade=35,
            veiculo_ano=2022,
            cep="01310100",
            plano_id="completo",
        ),
        extraction(
            Intent.AMBIGUOUS,
            ambiguous_fields=[LeadField.VEICULO_ANO],
        ),
        extraction(veiculo_ano=2021),
    ], [
        result(quote_id="old"),
        result(quote_id="new"),
    ])

    state = agent.handle(new_conversation_state(), "Cotação").state

    unclear = agent.handle(
        state,
        "O carro talvez seja 2021 ou 2022",
    )

    assert unclear.state["lead"].veiculo_ano is None
    assert unclear.state["quote_status"] == QuoteStatus.STALE
    assert unclear.state["quote_response"] is None
    assert unclear.state["quote_id"] is None
    assert unclear.state["next_action"] == TurnAction.ASK_INPUT
    assert "ano do veículo" in unclear.text
    assert len(client.quote_requests) == 1

    clarified = agent.handle(unclear.state, "É 2021")

    assert clarified.state["quote_status"] == QuoteStatus.SUCCEEDED
    assert clarified.state["quote_id"] == "new"
    assert len(client.quote_requests) == 2


def test_invalid_cep_requires_clarification_and_invalidates():
    agent, client = service([
        extraction(
            Intent.QUOTE,
            idade=35,
            veiculo_ano=2022,
            cep="01310100",
            plano_id="completo",
        ),
        extraction(cep="INVALIDO"),
    ], [result()])

    state = agent.handle(new_conversation_state(), "Cotação").state
    turn = agent.handle(state, "Corrigindo: CEP INVALIDO")

    assert turn.state["lead"].cep is None
    assert turn.state["quote_status"] == QuoteStatus.STALE
    assert turn.state["quote_response"] is None
    assert "CEP" in turn.text
    assert len(client.quote_requests) == 1


def test_human_request_preempts_quotation():
    agent, client = service([
        extraction(
            Intent.HUMAN,
            idade=35,
            veiculo_ano=2022,
            cep="01310100",
            plano_id="completo",
        ),
    ])

    turn = agent.handle(
        new_conversation_state(),
        "Quero falar com uma pessoa",
    )

    assert turn.state["next_action"] == TurnAction.HANDOFF
    assert turn.state["handoff_reason"] == HandoffReason.USER_REQUEST
    assert client.quote_requests == []


def test_out_of_scope_has_explicit_handoff_reason():
    agent, client = service([
        extraction(Intent.OUT_OF_SCOPE),
    ])

    turn = agent.handle(
        new_conversation_state(),
        "Preciso de um serviço não relacionado a seguros",
    )

    assert turn.state["next_action"] == TurnAction.HANDOFF
    assert turn.state["handoff_reason"] == HandoffReason.OUT_OF_SCOPE
    assert client.quote_requests == []


def test_commercial_decline_does_not_handoff():
    agent, client = service([
        extraction(
            Intent.QUOTE,
            idade=76,
            veiculo_ano=2022,
            cep="01310100",
            plano_id="completo",
        ),
    ], [
        result(
            ResultKind.COMMERCIAL_DECLINE,
            reason="Idade acima do limite de aceitacao",
        ),
    ])

    turn = agent.handle(new_conversation_state(), "Quero cotar")

    assert turn.state["quote_status"] == QuoteStatus.DECLINED
    assert turn.state["handoff_reason"] is None
    assert turn.state["next_action"] == TurnAction.EXPLAIN_DECLINE
    assert "Idade acima do limite" in turn.text
    assert len(client.quote_requests) == 1


def test_technical_failure_produces_handoff():
    agent, client = service([
        extraction(
            Intent.QUOTE,
            idade=35,
            veiculo_ano=2022,
            cep="01310100",
            plano_id="completo",
        ),
    ], [
        result(ResultKind.TIMEOUT),
    ])

    turn = agent.handle(new_conversation_state(), "Quero cotar")

    assert turn.state["next_action"] == TurnAction.HANDOFF
    assert turn.state["handoff_reason"] == HandoffReason.QUOTE_UNAVAILABLE
    assert turn.state["quote_response"] is None
    assert len(client.quote_requests) == 1


def test_information_before_quote_is_retained_without_quoting():
    agent, client = service([
        extraction(
            idade=35,
            veiculo_ano=2022,
            cep="01310100",
            plano_id="completo",
        ),
        extraction(Intent.QUOTE),
    ], [result()])

    first = agent.handle(
        new_conversation_state(),
        "Tenho 35, carro 2022...",
    )

    assert first.state["lead"].idade == 35
    assert not first.state["quote_flow_active"]
    assert client.quote_requests == []

    second = agent.handle(first.state, "Quero uma cotação")

    assert second.state["quote_status"] == QuoteStatus.SUCCEEDED
    assert len(client.quote_requests) == 1


def test_extraction_failure_does_not_mutate_state():
    agent, client = service([
        ExtractionError("simulated"),
    ])

    state = new_conversation_state()
    turn = agent.handle(state, "Quero cotar")

    assert turn.state is state
    assert turn.state["quote_status"] == QuoteStatus.NOT_REQUESTED
    assert client.quote_requests == []


def test_genuinely_unclear_message_asks_for_clarification():
    agent, client = service([
        extraction(Intent.AMBIGUOUS),
    ])

    turn = agent.handle(new_conversation_state(), "O outro")

    assert "esclarecer" in turn.text
    assert turn.state["quote_status"] == QuoteStatus.NOT_REQUESTED
    assert client.quote_requests == []


def test_extractor_calls_openai_structured_parse_without_history():
    class FakeResponses:
        def __init__(self):
            self.kwargs = None

        def parse(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                status="completed",
                output_parsed=extraction(
                    idade=36,
                    intent=Intent.INFORMATION,
                ),
            )

    responses = FakeResponses()
    sdk = SimpleNamespace(responses=responses)

    extractor = OpenAISemanticExtractor(client=sdk)
    state = new_conversation_state()
    state["quote_flow_active"] = True
    state["lead"] = LeadProfile(
        idade=35,
        plano_id="completo",
        cep="01310100",
    )

    parsed = extractor.extract("Agora tenho 36 anos", state)

    assert parsed.idade == 36
    assert parsed.cep is None
    assert responses.kwargs["text_format"] is SemanticExtraction
    assert responses.kwargs["store"] is False
    assert "01310100" not in responses.kwargs["input"]
    assert "Agora tenho 36 anos" in responses.kwargs["input"]


def test_missing_api_key_fails_fast(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAISemanticExtractor()
