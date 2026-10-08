
"""Unit tests: no Docker, outbound HTTP, or real sleeping."""

from collections import deque
from datetime import date
from decimal import Decimal

import httpx
import pytest

from autoseguro.quote_client import (
    QuoteClient,
    QuoteClientError,
    ResultKind,
    RetryPolicy,
)
from autoseguro.quote_contracts import QuoteRequest


@pytest.fixture
def quote_request():
    return QuoteRequest(
        plano_id="completo",
        idade=35,
        veiculo_ano=2022,
        cep="01310100",
        data_inicio=date(2026, 11, 15),
    )


@pytest.fixture
def success_body():
    return {
        "plano_id": "completo",
        "plano_nome": "Completo",
        "premio_mensal": 209.90,
        "franquia": 3000,
        "coberturas": ["colisao", "roubo", "furto"],
        "multiplicadores": {
            "faixa_etaria": 1.0,
            "idade_veiculo": 1.0,
            "regiao": 1.0,
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
            "valor_primeiro_pagamento": 111.95,
        },
    }


def client_for(responses, *, policy=None, sleep=None):
    """Responses are served in order; an exception simulates transport failure."""
    queue = deque(responses)
    requests = []

    def handler(request):
        requests.append(request)
        if not queue:
            raise AssertionError("Unexpected HTTP request")
        response = queue.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    client = QuoteClient(
        transport=httpx.MockTransport(handler),
        policy=policy or RetryPolicy(backoff_seconds=0),
        sleep=sleep or (lambda _: None),
    )
    return client, requests


def test_get_health_and_plans():
    client, requests = client_for([
        httpx.Response(200, json={"status": "ok"}),
        httpx.Response(200, json={
            "moeda": "BRL",
            "planos": [{
                "id": "completo",
                "nome": "Completo",
                "base_mensal": 209.9,
                "coberturas": ["roubo"],
                "franquia": 3000,
            }],
            "regras": {"carencia": {}},
        }),
    ])
    with client:
        assert client.get_health().status == "ok"
        assert client.get_plans().planos[0].id == "completo"
    assert [r.url.path for r in requests] == ["/health", "/planos"]


def test_success_and_serialization(quote_request, success_body):
    client, requests = client_for([httpx.Response(200, json=success_body)])
    with client:
        result = client.quote(quote_request)

    assert result.succeeded
    assert result.quote.premio_mensal == Decimal("209.90")
    assert result.quote.primeiro_pagamento_pro_rata.dias_cobrados == 16
    assert result.kind == ResultKind.SUCCESS
    assert len(result.attempts) == 1
    assert result.attempts[0].http_status == 200
    assert result.quote_id
    assert result.total_elapsed_ms >= 0
    assert requests[0].url.path == "/quote"
    assert requests[0].read().decode().count(
        '"data_inicio":"2026-11-15"'
    ) == 1


@pytest.mark.parametrize("status", [500, 502, 503])
def test_transient_error_then_success(status, quote_request, success_body):
    client, requests = client_for([
        httpx.Response(status, json={"error": "upstream_unavailable"}),
        httpx.Response(200, json=success_body),
    ])
    with client:
        result = client.quote(quote_request)

    assert result.succeeded
    assert [a.kind for a in result.attempts] == [
        ResultKind.TRANSIENT_ERROR, ResultKind.SUCCESS
    ]
    assert len(requests) == 2


@pytest.mark.parametrize("status", [500, 502, 503])
def test_transient_error_exhausts_retries(status, quote_request):
    client, requests = client_for([
        httpx.Response(status),
        httpx.Response(status),
        httpx.Response(status),
    ])
    with client:
        result = client.quote(quote_request)

    assert not result.succeeded
    assert result.quote is None
    assert result.kind == ResultKind.TRANSIENT_ERROR
    assert len(result.attempts) == len(requests) == 3


def test_timeout_then_success(quote_request, success_body):
    client, requests = client_for([
        httpx.ReadTimeout("simulated"),
        httpx.Response(200, json=success_body),
    ])
    with client:
        result = client.quote(quote_request)

    assert result.succeeded
    assert [a.kind for a in result.attempts] == [
        ResultKind.TIMEOUT, ResultKind.SUCCESS
    ]
    assert len(requests) == 2


def test_timeout_exhausts_retries(quote_request):
    client, requests = client_for([httpx.ReadTimeout("slow")] * 3)
    with client:
        result = client.quote(quote_request)

    assert result.kind == ResultKind.TIMEOUT
    assert result.quote is None
    assert len(requests) == 3


def test_connection_error_then_success(quote_request, success_body):
    client, _ = client_for([
        httpx.ConnectError("offline"),
        httpx.Response(200, json=success_body),
    ])
    with client:
        result = client.quote(quote_request)

    assert result.succeeded
    assert result.attempts[0].kind == ResultKind.TRANSPORT_ERROR


@pytest.mark.parametrize("status,body,expected", [
    (
        422,
        {"error": "cotacao_recusada", "motivo": "Idade fora da faixa"},
        ResultKind.COMMERCIAL_DECLINE,
    ),
    (
        422,
        {"detail": [{
            "loc": ["body", "idade"],
            "type": "missing",
            "msg": "Required",
        }]},
        ResultKind.SCHEMA_VALIDATION,
    ),
    (
        400,
        {"error": "payload_invalido", "detalhe": "Data invalida"},
        ResultKind.INVALID_PAYLOAD,
    ),
    (
        422,
        {"unexpected": "format"},
        ResultKind.UNEXPECTED_HTTP,
    ),
    (
        401,
        {"error": "unauthorized"},
        ResultKind.UNEXPECTED_HTTP,
    ),
])
def test_non_retryable_errors(status, body, expected, quote_request):
    client, requests = client_for([httpx.Response(status, json=body)])
    with client:
        result = client.quote(quote_request)

    assert result.kind == expected
    assert result.quote is None
    assert len(result.attempts) == len(requests) == 1
    assert result.decline_reason == (
        "Idade fora da faixa"
        if expected == ResultKind.COMMERCIAL_DECLINE
        else None
    )


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"premio_mensal": 209.9}),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"premio_mensal": -1}),
])
def test_invalid_success_response_never_quotes(response, quote_request):
    client, requests = client_for([response])
    with client:
        result = client.quote(quote_request)

    assert result.kind == ResultKind.INVALID_RESPONSE
    assert result.quote is None
    assert not result.succeeded
    assert len(requests) == 1


def test_exponential_backoff(quote_request, success_body):
    delays = []
    client, _ = client_for([
        httpx.Response(503),
        httpx.Response(502),
        httpx.Response(200, json=success_body),
    ], policy=RetryPolicy(backoff_seconds=0.25), sleep=delays.append)

    with client:
        result = client.quote(quote_request)

    assert result.succeeded
    assert delays == [0.25, 0.5]


def test_get_transport_error_does_not_leak_payload():
    client, _ = client_for([httpx.ConnectError("private data")])
    with client, pytest.raises(QuoteClientError) as caught:
        client.get_plans()

    assert caught.value.kind == ResultKind.TRANSPORT_ERROR
    assert "private data" not in str(caught.value)
