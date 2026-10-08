
"""Tests for the interactive CLI. No network access required."""

import pytest

from autoseguro import cli
from autoseguro.conversation import ConversationTurn
from autoseguro.state import new_conversation_state


class FakeService:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def handle(self, state, message):
        self.calls.append({
            "conversation_id": state["conversation_id"],
            "active_before": state["quote_flow_active"],
            "message": message,
        })

        if message == self.fail_on:
            raise RuntimeError("sensitive internal error")

        updated = {
            **state,
            "quote_flow_active": True,
        }

        return ConversationTurn(
            state=updated,
            text=f"Mensagem recebida: {len(self.calls)}",
        )


def scripted_input(messages):
    iterator = iter(messages)
    return lambda prompt: next(iterator)


def test_cli_preserves_state_between_messages():
    service = FakeService()
    output = []

    cli.run_cli(
        service,
        input_fn=scripted_input([
            "Quero cotar um seguro",
            "Tenho 35 anos",
            "sair",
        ]),
        output_fn=output.append,
    )

    assert len(service.calls) == 2

    first, second = service.calls

    assert first["conversation_id"] == second["conversation_id"]
    assert first["active_before"] is False
    assert second["active_before"] is True

    assert any("Mensagem recebida: 1" in x for x in output)
    assert any("Mensagem recebida: 2" in x for x in output)
    assert output[-1] == "Sessão encerrada."


@pytest.mark.parametrize("exit_command", [
    "sair",
    "SAIR",
    " Sair ",
])
def test_exit_command(exit_command):
    service = FakeService()
    output = []

    cli.run_cli(
        service,
        input_fn=scripted_input([exit_command]),
        output_fn=output.append,
    )

    assert service.calls == []
    assert output[-1] == "Sessão encerrada."


@pytest.mark.parametrize("exception", [
    EOFError(),
    KeyboardInterrupt(),
])
def test_terminal_interruption_exits_cleanly(exception):
    service = FakeService()
    output = []

    def interrupted_input(prompt):
        raise exception

    cli.run_cli(
        service,
        input_fn=interrupted_input,
        output_fn=output.append,
    )

    assert service.calls == []
    assert "Sessão encerrada." in output[-1]


def test_internal_error_does_not_commit_failed_turn():
    service = FakeService(fail_on="erro")
    output = []

    cli.run_cli(
        service,
        input_fn=scripted_input([
            "Primeira mensagem",
            "erro",
            "Terceira mensagem",
            "sair",
        ]),
        output_fn=output.append,
    )

    assert len(service.calls) == 3

    ids = {call["conversation_id"] for call in service.calls}
    assert len(ids) == 1

    assert service.calls[2]["active_before"] is True

    combined = "\n".join(output)
    assert "falha interna" in combined
    assert "sensitive internal error" not in combined


def test_missing_api_key_prevents_startup(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    code = cli.main()

    assert code == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err


def test_main_wires_existing_components(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")

    created = {}

    class FakeQuoteClient:
        def __enter__(self):
            created["client_entered"] = True
            return self

        def __exit__(self, *args):
            created["client_closed"] = True

    def fake_build_graph(client):
        created["graph_client"] = client
        return "fake_graph"

    def fake_extractor():
        return "fake_extractor"

    def fake_conversation_service(graph, extractor):
        created["graph"] = graph
        created["extractor"] = extractor
        return "fake_service"

    def fake_run_cli(service):
        created["service"] = service

    monkeypatch.setattr(cli, "QuoteClient", FakeQuoteClient)
    monkeypatch.setattr(cli, "build_graph", fake_build_graph)
    monkeypatch.setattr(
        cli, "OpenAISemanticExtractor", fake_extractor
    )
    monkeypatch.setattr(
        cli, "ConversationService", fake_conversation_service
    )
    monkeypatch.setattr(cli, "run_cli", fake_run_cli)

    assert cli.main() == 0
    assert created["client_entered"]
    assert created["client_closed"]
    assert created["graph"] == "fake_graph"
    assert created["extractor"] == "fake_extractor"
    assert created["service"] == "fake_service"
