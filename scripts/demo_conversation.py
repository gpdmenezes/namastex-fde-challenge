
"""Run and record a real end-to-end synthetic conversation."""

import os
import sys
from datetime import date, timedelta

from autoseguro.conversation import ConversationService
from autoseguro.extraction import OpenAISemanticExtractor
from autoseguro.observability import TurnRecorder
from autoseguro.quote_client import QuoteClient
from autoseguro.state import (
    HandoffReason,
    QuoteStatus,
    new_conversation_state,
)
from autoseguro.workflow import build_graph


def demo_messages(today: date | None = None) -> list[str]:
    today = today or date.today()
    next_month = (
        today.replace(day=1) + timedelta(days=32)
    ).replace(day=15)

    return [
        "Olá, quero cotar um seguro para meu carro.",
        "Tenho 35 anos e meu carro é de 2022.",
        (
            "Meu CEP é 01310-100, quero o plano completo "
            f"e início em {next_month.isoformat()}."
        ),
        "Na verdade, corrigindo: meu carro é de 2020.",
        "Quero falar com um atendente humano.",
    ]


def run_demo(
    service,
    recorder: TurnRecorder,
    messages: list[str] | None = None,
) -> bool:
    """Execute actual service turns; return False for incomplete runs."""

    messages = messages if messages is not None else demo_messages()
    state = new_conversation_state()
    successful_quotes: set[str] = set()

    recorder.demo_status("started")

    for index, message in enumerate(messages, start=1):
        previous_quote_id = state["quote_id"]

        turn = recorder.record_turn(service, state, message)
        state = turn.state

        if (
            state["quote_status"] == QuoteStatus.SUCCEEDED
            and state["quote_id"] is not None
            and state["quote_id"] != previous_quote_id
        ):
            successful_quotes.add(state["quote_id"])

        print(
            f"Turno {index}: "
            f"action={state['next_action']} "
            f"quote_status={state['quote_status']}"
        )

    completed = (
        len(successful_quotes) >= 2
        and state["handoff_reason"] == HandoffReason.USER_REQUEST
    )

    recorder.demo_status(
        "completed" if completed else "failed",
        reason_code=None if completed else "incomplete_flow",
    )

    return completed


def main() -> int:
    recorder = TurnRecorder(
        audit_path="logs/operational.jsonl",
        evidence_path="evidence/demo_conversation.jsonl",
    )

    # Never mix evidence from different runs.
    recorder.reset_evidence()

    if not os.environ.get("OPENAI_API_KEY"):
        recorder.demo_status(
            "failed",
            reason_code="missing_api_key",
        )
        print(
            "OPENAI_API_KEY não configurada.",
            file=sys.stderr,
        )
        return 2

    try:
        with QuoteClient() as quote_client:
            graph = build_graph(quote_client)
            extractor = OpenAISemanticExtractor()
            service = ConversationService(graph, extractor)

            completed = run_demo(service, recorder)

    except Exception as exc:
        recorder.demo_status(
            "failed",
            reason_code="runtime_error",
            error_type=type(exc).__name__,
        )
        print(
            "Demonstração interrompida; falha registrada "
            "na evidência.",
            file=sys.stderr,
        )
        return 1

    if not completed:
        print(
            "Demonstração incompleta. Consulte a evidência "
            "para identificar o resultado real.",
            file=sys.stderr,
        )
        return 1

    print(
        "Demonstração concluída. Evidência: "
        "evidence/demo_conversation.jsonl"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
