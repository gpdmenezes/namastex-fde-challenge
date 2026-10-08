
"""Tests for operational audit and synthetic evidence recording."""

import json

import pytest

from autoseguro.conversation import ConversationTurn
from autoseguro.observability import TurnRecorder
from autoseguro.quote_client import QuoteAttempt, ResultKind
from autoseguro.quote_contracts import QuoteRequest, QuoteResponse
from autoseguro.state import (
    QuoteStatus,
    TurnAction,
    new_conversation_state,
)


def read_events(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def successful_quote():
    return QuoteResponse.model_validate({
        "plano_id": "completo",
        "plano_nome": "Completo",
        "premio_mensal": "209.90",
        "franquia": 3000,
        "coberturas": ["roubo", "furto"],
        "multiplicadores": {
            "faixa_etaria": 1,
            "idade_veiculo": 1,
            "regiao": 1,
        },
        "carencia": {
            "coberturas": ["roubo", "furto"],
            "dias": 30,
            "observacao": "Carência",
        },
        "moeda": "BRL",
    })


class FakeSuccessfulService:
    def handle(self, state, message):
        updated = {
            **state,
            "quote_id": "q-123",
            "quote_status": QuoteStatus.SUCCEEDED,
            "quote_request": QuoteRequest(
                plano_id="completo",
                idade=35,
                veiculo_ano=2022,
                cep="01310100",
            ),
            "quote_response": successful_quote(),
            "quote_attempts": (
                QuoteAttempt(
                    number=1,
                    http_status=503,
                    elapsed_ms=44,
                    kind=ResultKind.TRANSIENT_ERROR,
                ),
                QuoteAttempt(
                    number=2,
                    http_status=200,
                    elapsed_ms=46,
                    kind=ResultKind.SUCCESS,
                ),
            ),
            "next_action": TurnAction.SHOW_QUOTE,
        }

        return ConversationTurn(
            updated,
            "Cotação válida: R$ 209,90.",
        )


def test_operational_logs_exclude_message_content_and_pii(tmp_path):
    operational = tmp_path / "operational.jsonl"
    recorder = TurnRecorder(operational)
    state = new_conversation_state()

    sensitive = (
        "CPF 123.456.789-00, email ana@example.com "
        "e chave sk-test-secret"
    )

    recorder.record_turn(
        FakeSuccessfulService(), state, sensitive
    )

    contents = operational.read_text(encoding="utf-8")

    assert sensitive not in contents
    assert "123.456.789-00" not in contents
    assert "ana@example.com" not in contents
    assert "sk-test-secret" not in contents
    assert "01310100" not in contents
    assert "209.90" not in contents

    events = read_events(operational)

    assert any(e["event"] == "workflow_decision" for e in events)
    assert any(e["event"] == "quote_result" for e in events)
    assert all(e["event_id"] for e in events)
    assert all(e["timestamp"] for e in events)


def test_quote_attempts_share_correlation_ids(tmp_path):
    path = tmp_path / "operational.jsonl"
    recorder = TurnRecorder(path)
    state = new_conversation_state()

    recorder.record_turn(
        FakeSuccessfulService(), state, "Cotação"
    )

    events = read_events(path)
    attempts = [
        event for event in events
        if event["event"] == "quote_attempt"
    ]

    assert len(attempts) == 2
    assert [e["attempt_number"] for e in attempts] == [1, 2]
    assert [e["http_status"] for e in attempts] == [503, 200]
    assert [e["elapsed_ms"] for e in attempts] == [44, 46]
    assert {e["quote_id"] for e in attempts} == {"q-123"}
    assert len({e["turn_id"] for e in attempts}) == 1
    assert all(
        e["conversation_id"] == state["conversation_id"]
        for e in attempts
    )


def test_existing_quote_attempts_are_not_logged_again(tmp_path):
    path = tmp_path / "operational.jsonl"
    recorder = TurnRecorder(path)
    service = FakeSuccessfulService()

    first = recorder.record_turn(
        service, new_conversation_state(), "Cotar"
    )
    recorder.record_turn(service, first.state, "E agora?")

    attempts = [
        event for event in read_events(path)
        if event["event"] == "quote_attempt"
    ]
    assert len(attempts) == 2


def test_synthetic_evidence_has_actual_inputs_and_outputs(tmp_path):
    audit = tmp_path / "operational.jsonl"
    evidence = tmp_path / "evidence.jsonl"

    recorder = TurnRecorder(audit, evidence)
    recorder.reset_evidence()

    recorder.record_turn(
        FakeSuccessfulService(),
        new_conversation_state(),
        "Quero uma cotação sintética",
    )

    events = read_events(evidence)
    messages = [
        e for e in events if e["event"] == "message"
    ]

    assert len(messages) == 2
    assert messages[0]["content"] == "Quero uma cotação sintética"
    assert messages[1]["content"] == "Cotação válida: R$ 209,90."
    assert messages[1]["reply_to_message_id"] == messages[0]["message_id"]

    quote_event = next(
        e for e in events if e["event"] == "quote_result"
    )
    assert quote_event["response"]["premio_mensal"] == "209.90"
    assert quote_event["request"]["plano_id"] == "completo"


def test_exceptions_record_failure_without_exception_message(tmp_path):
    class FailingService:
        def handle(self, state, message):
            raise RuntimeError("private-token-abc123")

    audit = tmp_path / "operational.jsonl"
    evidence = tmp_path / "evidence.jsonl"
    recorder = TurnRecorder(audit, evidence)

    with pytest.raises(RuntimeError):
        recorder.record_turn(
            FailingService(),
            new_conversation_state(),
            "Mensagem sintética",
        )

    audit_text = audit.read_text(encoding="utf-8")
    assert "private-token-abc123" not in audit_text

    events = read_events(audit)
    assert any(
        e["event"] == "message_status"
        and e["status"] == "failed"
        and e["error_type"] == "RuntimeError"
        for e in events
    )

    evidence_events = read_events(evidence)
    assert any(
        e["event"] == "turn_failed"
        for e in evidence_events
    )
    assert not any(
        e["event"] == "message"
        and e["role"] == "assistant"
        for e in evidence_events
    )


def test_operational_allowlist_rejects_content(tmp_path):
    recorder = TurnRecorder(tmp_path / "operational.jsonl")

    with pytest.raises(ValueError, match="Disallowed"):
        recorder._audit("message_status", content="sensitive")
