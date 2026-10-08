
"""Interactive CLI for the AutoSeguro conversational agent."""

import os
import sys
from collections.abc import Callable

from .conversation import ConversationService
from .extraction import OpenAISemanticExtractor
from .observability import TurnRecorder
from .quote_client import QuoteClient
from .state import new_conversation_state
from .workflow import build_graph


def run_cli(
    service: ConversationService,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    recorder: TurnRecorder | None = None,
) -> None:
    """Run one in-memory conversation until the user exits."""

    state = new_conversation_state()

    # Operational logging for the real CLI only.
    if recorder is None and isinstance(service, ConversationService):
        recorder = TurnRecorder("logs/operational.jsonl")

    output_fn("=== AutoSeguro ===")
    output_fn(
        "Olá! Posso ajudar com sua cotação de seguro automotivo."
    )
    output_fn("Digite 'sair' para encerrar.\n")

    while True:
        try:
            message = input_fn("Você> ")
        except (EOFError, KeyboardInterrupt):
            output_fn("\nSessão encerrada.")
            return

        if message.strip().casefold() == "sair":
            output_fn("Sessão encerrada.")
            return

        try:
            if recorder is not None:
                turn = recorder.record_turn(service, state, message)
            else:
                turn = service.handle(state, message)
        except KeyboardInterrupt:
            output_fn("\nSessão encerrada.")
            return
        except Exception:
            # No exception messages, payloads or credentials in stdout.
            output_fn(
                "AutoSeguro> Ocorreu uma falha interna ao processar "
                "sua mensagem. Tente novamente."
            )
            continue

        state = turn.state
        output_fn(f"\nAutoSeguro> {turn.text}\n")


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "Erro: OPENAI_API_KEY não está configurada.",
            file=sys.stderr,
        )
        return 2

    try:
        with QuoteClient() as quote_client:
            graph = build_graph(quote_client)
            extractor = OpenAISemanticExtractor()
            service = ConversationService(graph, extractor)
            run_cli(service)
    except Exception:
        print(
            "Não foi possível inicializar o atendimento.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
