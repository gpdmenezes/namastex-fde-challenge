
"""Multi-turn conversational adapter and deterministic response rendering."""

from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from .domain import LeadPatch
from .extraction import (
    ExtractionError,
    Intent,
    LeadField,
    SemanticExtraction,
    SemanticExtractor,
)
from .qualification import QualificationStatus
from .state import (
    ConversationState,
    HandoffReason,
    QuoteStatus,
    TurnAction,
    apply_lead_patch,
)
from .workflow import run_turn


FIELD_LABELS = {
    "plano_id": "plano desejado",
    "idade": "idade do motorista",
    "veiculo_ano": "ano do veículo",
    "cep": "CEP",
    "data_inicio": "data de início",
}

LEAD_FIELDS = tuple(FIELD_LABELS)


@dataclass(frozen=True)
class ConversationTurn:
    state: ConversationState
    text: str


def _money(value: Decimal) -> str:
    formatted = f"{value:,.2f}"
    formatted = formatted.replace(",", "#").replace(".", ",")
    return "R$ " + formatted.replace("#", ".")


def _make_patch(
    extracted: SemanticExtraction,
) -> tuple[LeadPatch, tuple[str, ...]]:
    """Validate fields separately, retaining independent valid values."""

    ambiguous = {field.value for field in extracted.ambiguous_fields}
    explicit_clear = {field.value for field in extracted.clear_fields}

    updates = {}
    invalid = []

    for field in LEAD_FIELDS:
        if field in ambiguous or field in explicit_clear:
            continue

        value = getattr(extracted, field)
        if value is None:
            continue

        try:
            validated = LeadPatch.model_validate({field: value})
        except ValidationError:
            invalid.append(field)
            continue

        updates[field] = getattr(validated, field)

    # An uncertain or invalid correction must not leave an old
    # quote eligible for reuse.
    clear_fields = tuple(sorted(
        ambiguous | explicit_clear | set(invalid)
    ))

    patch = LeadPatch(
        **updates,
        clear_fields=clear_fields,
    )

    issues = tuple(sorted(ambiguous | set(invalid)))
    return patch, issues


def _clarification(fields: tuple[str, ...]) -> str:
    if not fields:
        return (
            "Não consegui entender com segurança sua solicitação. "
            "Pode esclarecer o que deseja?"
        )

    labels = ", ".join(FIELD_LABELS[field] for field in fields)
    return f"Preciso confirmar: {labels}. Pode informar novamente?"


def _missing_information(state: ConversationState) -> str:
    result = state["qualification"]
    if result is None:
        return "Quais informações você deseja fornecer para a cotação?"

    parts = []

    if result.missing_fields:
        labels = ", ".join(
            FIELD_LABELS[field] for field in result.missing_fields
        )
        parts.append(f"Para cotar, preciso de: {labels}.")

    if result.invalid_fields:
        labels = ", ".join(
            FIELD_LABELS[field] for field in result.invalid_fields
        )
        parts.append(f"Preciso corrigir: {labels}.")

        if "plano_id" in result.invalid_fields and state["catalog"]:
            names = ", ".join(
                plan.nome for plan in state["catalog"].planos
            )
            parts.append(f"Planos disponíveis: {names}.")

    return " ".join(parts) or "Preciso de mais informações para cotar."


HANDOFF_MESSAGES = {
    HandoffReason.USER_REQUEST:
        "Você solicitou atendimento humano.",
    HandoffReason.OUT_OF_SCOPE:
        "Sua solicitação precisa de atendimento fora deste fluxo de cotação.",
    HandoffReason.QUOTE_UNAVAILABLE:
        "Não consegui obter a cotação após as tentativas de recuperação.",
    HandoffReason.CATALOG_UNAVAILABLE:
        "Não consegui consultar os planos disponíveis.",
    HandoffReason.INTEGRATION_ERROR:
        "Ocorreu uma inconsistência técnica na integração.",
}


def render_response(state: ConversationState) -> str:
    """Never generate commercial figures with the LLM."""

    action = state["next_action"]

    if action == TurnAction.ASK_INPUT:
        return _missing_information(state)

    if action == TurnAction.SHOW_QUOTE:
        quote = state["quote_response"]

        if (
            state["quote_status"] != QuoteStatus.SUCCEEDED
            or quote is None
        ):
            raise RuntimeError("No valid quote to present")

        lines = [
            f"Cotação do plano {quote.plano_nome}:",
            f"Mensalidade: {_money(quote.premio_mensal)}.",
            f"Franquia: {_money(quote.franquia)}.",
            "Coberturas: " + ", ".join(quote.coberturas) + ".",
        ]

        waiting = quote.carencia
        if waiting.coberturas:
            lines.append(
                f"Carência: {waiting.dias} dias para "
                + ", ".join(waiting.coberturas)
                + "."
            )

        if quote.primeiro_pagamento_pro_rata is not None:
            first = quote.primeiro_pagamento_pro_rata
            lines.append(
                "Primeiro pagamento proporcional: "
                + _money(first.valor_primeiro_pagamento)
                + f" ({first.dias_cobrados} dias cobrados)."
            )

        return "\n".join(lines)

    if action == TurnAction.EXPLAIN_DECLINE:
        if state["quote_status"] != QuoteStatus.DECLINED:
            raise RuntimeError("No commercial decline to explain")

        reason = state["decline_reason"]
        return (
            f"Não foi possível aprovar a cotação. Motivo: {reason}"
            if reason
            else "A API recusou a cotação para os dados informados."
        )

    if action == TurnAction.HANDOFF:
        reason = state["handoff_reason"]

        if reason is None:
            raise RuntimeError("Handoff has no reason")

        return (
            HANDOFF_MESSAGES[reason]
            + " O caso foi marcado para encaminhamento humano, "
            "mas ainda não existe integração com atendentes."
        )

    raise RuntimeError(f"Unsupported turn action: {action}")


class ConversationService:
    def __init__(self, graph, extractor: SemanticExtractor) -> None:
        self._graph = graph
        self._extractor = extractor

    def handle(
        self,
        state: ConversationState,
        message: str,
    ) -> ConversationTurn:
        """Process one message and return the updated state."""

        # Handoff is sticky until an explicit future resolution.
        if state["handoff_reason"] is not None:
            return ConversationTurn(state, render_response(state))

        if not message.strip():
            return ConversationTurn(
                state,
                "Pode escrever sua mensagem para continuarmos?",
            )

        try:
            extracted = self._extractor.extract(message, state)
        except ExtractionError:
            return ConversationTurn(
                state,
                "Não consegui interpretar sua mensagem agora. "
                "Pode tentar novamente?",
            )

        patch, issues = _make_patch(extracted)

        active = (
            state["quote_flow_active"]
            or extracted.intent == Intent.QUOTE
        )

        working_state = {
            **state,
            "quote_flow_active": active,
        }

        # Explicit handoff has priority over collecting or quoting.
        if extracted.intent == Intent.HUMAN:
            updated = run_turn(
                self._graph,
                working_state,
                patch=patch,
                request_handoff=True,
            )
            return ConversationTurn(updated, render_response(updated))

        if extracted.intent == Intent.OUT_OF_SCOPE:
            updated = run_turn(
                self._graph,
                {
                    **working_state,
                    "handoff_reason": HandoffReason.OUT_OF_SCOPE,
                },
            )
            return ConversationTurn(updated, render_response(updated))

        # Keep valid independent data, but stop before quoting
        # when any field requires clarification.
        if issues:
            updates = apply_lead_patch(working_state, patch)
            updated = {
                **working_state,
                **updates,
                "next_action": TurnAction.ASK_INPUT,
            }
            return ConversationTurn(updated, _clarification(issues))

        has_data = bool(
            patch.model_dump(
                exclude_none=True,
                exclude={"clear_fields"},
            )
            or patch.clear_fields
        )

        # Partial information with no explicit intent is not ambiguity.
        if extracted.intent == Intent.AMBIGUOUS and not has_data:
            return ConversationTurn(
                working_state,
                _clarification(()),
            )

        if extracted.intent == Intent.GREETING and not active and not has_data:
            return ConversationTurn(
                working_state,
                "Olá! Posso ajudar com uma cotação de seguro automotivo.",
            )

        # Unsolicited information is retained but does not trigger a
        # quotation until a quote flow has actually started.
        if not active:
            updates = apply_lead_patch(working_state, patch)
            updated = {**working_state, **updates}
            return ConversationTurn(
                updated,
                "Informações recebidas. Deseja iniciar uma cotação "
                "de seguro automotivo?",
            )

        updated = run_turn(
            self._graph,
            working_state,
            patch=patch,
        )
        return ConversationTurn(updated, render_response(updated))
