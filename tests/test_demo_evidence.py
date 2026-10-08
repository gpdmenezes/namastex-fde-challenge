
"""Tests that demo evidence comes from executed service turns."""

import json

from autoseguro.conversation import ConversationTurn
from autoseguro.observability import TurnRecorder
from autoseguro.quote_client import QuoteAttempt, ResultKind
from autoseguro.quote_contracts import QuoteRequest, QuoteResponse
from autoseguro.state import (
    HandoffReason,
    QuoteStatus,
    TurnAction,
)
from scripts.demo_conversation import run_demo


def read_events(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def quote_response(price):
    return QuoteResponse.model_validate({
        "plano_id": "completo",
        "plano_nome": "Completo",
        "premio_mensal": price,
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


class FakeDemoService:
    def __init__(self):
        self.calls = 0

    def handle(self, state, message):
        self.calls += 1
        updated = {
            **state,
            "quote_flow_active": True,
        }

        if self.calls in (3, 4):
            year = 2022 if self.calls == 3 else 2020
            quote_id = f"quote-{self.calls}"

            updated.update(
                quote_id=quote_id,
                quote_status=QuoteStatus.SUCCEEDED,
                next_action=TurnAction.SHOW_QUOTE,
                quote_request=QuoteRequest(
                    plano_id="completo",
                    idade=35,
                    veiculo_ano=year,
                    cep="01310100",
                ),
                quote_response=quote_response(
                    "209.90" if self.calls == 3 else "241.38"
                ),
                quote_error_kind=None,
                quote_attempts=(
                    QuoteAttempt(
                        number=1,
                        http_status=200,
                        elapsed_ms=44,
                        kind=ResultKind.SUCCESS,
                    ),
                ),
            )
        elif self.calls == 5:
            updated.update(
                next_action=TurnAction.HANDOFF,
                handoff_reason=HandoffReason.USER_REQUEST,
            )
        else:
            updated["next_action"] = TurnAction.ASK_INPUT

        return ConversationTurn(
            updated,
            f"Resposta produzida pelo double no turno {self.calls}",
        )


class FakeIncompleteService:
    def handle(self, state, message):
        updated = {
            **state,
            "next_action": TurnAction.HANDOFF,
            "handoff_reason": HandoffReason.QUOTE_UNAVAILABLE,
            "quote_status": QuoteStatus.UNAVAILABLE,
        }
        return ConversationTurn(
            updated,
            "Cotação indisponível.",
        )


def test_demo_records_executed_conversation(tmp_path):
    evidence = tmp_path / "demo.jsonl"
    recorder = TurnRecorder(
        tmp_path / "operational.jsonl",
        evidence,
    )
    recorder.reset_evidence()

    messages = [
        "Quero seguro",
        "Tenho 35 anos, carro 2022",
        "CEP 01310-100, plano completo",
        "Na verdade, carro 2020",
        "Quero falar com humano",
    ]

    service = FakeDemoService()

    assert run_demo(service, recorder, messages)
    assert service.calls == 5

    events = read_events(evidence)
    user_messages = [
        e for e in events
        if e["event"] == "message" and e["role"] == "user"
    ]
    assistant_messages = [
        e for e in events
        if e["event"] == "message" and e["role"] == "assistant"
    ]

    assert [e["content"] for e in user_messages] == messages
    assert len(assistant_messages) == 5

    assert assistant_messages[2]["content"] == (
        "Resposta produzida pelo double no turno 3"
    )

    quotes = [
        e for e in events if e["event"] == "quote_result"
    ]

    assert len(quotes) == 2
    assert [q["quote_id"] for q in quotes] == [
        "quote-3", "quote-4"
    ]
    assert quotes[1]["request"]["veiculo_ano"] == 2020
    assert quotes[1]["response"]["premio_mensal"] == "241.38"

    assert any(
        e["event"] == "handoff"
        and e["status"] == "recorded_not_dispatched"
        for e in events
    )

    assert events[-1]["event"] == "demo_status"
    assert events[-1]["status"] == "completed"


def test_incomplete_demo_is_not_reported_as_success(tmp_path):
    evidence = tmp_path / "demo.jsonl"
    recorder = TurnRecorder(
        tmp_path / "operational.jsonl",
        evidence,
    )
    recorder.reset_evidence()

    completed = run_demo(
        FakeIncompleteService(),
        recorder,
        ["Solicito uma cotação"],
    )

    assert completed is False

    events = read_events(evidence)

    assert events[-1]["status"] == "failed"
    assert events[-1]["reason_code"] == "incomplete_flow"
    assert not any(
        e["event"] == "quote_result"
        and e.get("quote_status") == "succeeded"
        for e in events
    )
