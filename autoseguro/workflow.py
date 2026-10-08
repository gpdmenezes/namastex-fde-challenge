
"""Deterministic LangGraph workflow for one conversation turn."""

from langgraph.graph import END, START, StateGraph

from .domain import LeadPatch
from .qualification import QualificationStatus
from .quote_client import (
    QuoteClient,
    QuoteClientError,
    ResultKind,
)
from .quote_contracts import QuoteRequest
from .state import (
    ConversationState,
    HandoffReason,
    QuoteStatus,
    TurnAction,
    apply_lead_patch,
    qualification_node,
)


TECHNICAL_FAILURES = frozenset({
    ResultKind.TRANSIENT_ERROR,
    ResultKind.TIMEOUT,
    ResultKind.TRANSPORT_ERROR,
})


def _ingest_node(state: ConversationState) -> dict:
    """Consume structured input for the current turn."""

    update = {
        "pending_patch": None,
        "handoff_requested": False,
        "next_action": None,
    }

    patch = state["pending_patch"]

    if patch is not None:
        update.update(apply_lead_patch(state, patch))

    if state["handoff_requested"]:
        update["handoff_reason"] = HandoffReason.USER_REQUEST

    return update


def _route_ingest(state: ConversationState) -> str:
    if state["handoff_reason"] is not None:
        return "handoff"

    return "qualify"


def _route_qualification(state: ConversationState) -> str:
    result = state["qualification"]

    if state["handoff_reason"] is not None:
        return "handoff"

    if result is None:
        raise RuntimeError("Qualification was not executed")

    if result.status == QualificationStatus.NEEDS_INPUT:
        return "respond"

    if result.status == QualificationStatus.CATALOG_UNAVAILABLE:
        return "load_catalog"

    if result.status != QualificationStatus.READY:
        raise RuntimeError(f"Unexpected qualification: {result.status}")

    # READY means QuoteRequest can now be constructed.
    request = QuoteRequest.from_lead(state["lead"])

    # Reuse only the result for the exact same normalized request.
    if state["quote_request"] == request:
        if (
            state["quote_status"] == QuoteStatus.SUCCEEDED
            and state["quote_response"] is not None
        ):
            return "respond"

        if state["quote_status"] == QuoteStatus.DECLINED:
            return "respond"

    return "quote"


def _route_after_catalog(state: ConversationState) -> str:
    if state["handoff_reason"] is not None:
        return "handoff"

    result = state["qualification"]

    if result is None:
        raise RuntimeError("Qualification was not executed")

    # Never reload the catalog repeatedly in the same turn.
    if result.status == QualificationStatus.CATALOG_UNAVAILABLE:
        return "handoff"

    return _route_qualification(state)


def _route_after_quote(state: ConversationState) -> str:
    if state["handoff_reason"] is not None:
        return "handoff"

    return "respond"


def _respond_node(state: ConversationState) -> dict:
    qualification = state["qualification"]

    if (
        qualification is not None
        and qualification.status == QualificationStatus.NEEDS_INPUT
    ):
        return {"next_action": TurnAction.ASK_INPUT}

    if (
        state["quote_status"] == QuoteStatus.SUCCEEDED
        and state["quote_response"] is not None
    ):
        return {"next_action": TurnAction.SHOW_QUOTE}

    if state["quote_status"] == QuoteStatus.DECLINED:
        return {"next_action": TurnAction.EXPLAIN_DECLINE}

    raise RuntimeError("No valid response action for current state")


def _handoff_node(state: ConversationState) -> dict:
    # A missing reason here indicates an invalid graph transition.
    reason = state["handoff_reason"]

    if reason is None:
        reason = HandoffReason.INTEGRATION_ERROR

    return {
        "handoff_reason": reason,
        "next_action": TurnAction.HANDOFF,
    }


def build_graph(client: QuoteClient):
    """Compile one graph with an injected quote client."""

    def load_catalog(state: ConversationState) -> dict:
        try:
            catalog = client.get_plans()
        except QuoteClientError:
            return {
                "catalog": None,
                "handoff_reason": HandoffReason.CATALOG_UNAVAILABLE,
            }

        if not catalog.planos:
            return {
                "catalog": None,
                "handoff_reason": HandoffReason.CATALOG_UNAVAILABLE,
            }

        return {"catalog": catalog}

    def request_quote(state: ConversationState) -> dict:
        request = QuoteRequest.from_lead(state["lead"])
        result = client.quote(request)

        update = {
            "quote_id": result.quote_id,
            "quote_request": request,
            "quote_response": None,
            "quote_attempts": result.attempts,
            "quote_error_kind": None,
            "decline_reason": None,
        }

        # Do not trust success unless the parsed response exists
        # and belongs to the requested plan.
        if result.succeeded:
            if result.quote.plano_id == request.plano_id:
                update.update(
                    quote_response=result.quote,
                    quote_status=QuoteStatus.SUCCEEDED,
                )
                return update

            update.update(
                quote_status=QuoteStatus.UNAVAILABLE,
                quote_error_kind=ResultKind.INVALID_RESPONSE,
                handoff_reason=HandoffReason.INTEGRATION_ERROR,
            )
            return update

        if result.kind == ResultKind.COMMERCIAL_DECLINE:
            update.update(
                quote_status=QuoteStatus.DECLINED,
                decline_reason=result.decline_reason,
                quote_error_kind=result.kind,
            )
            return update

        update.update(
            quote_status=QuoteStatus.UNAVAILABLE,
            quote_error_kind=result.kind,
            handoff_reason=(
                HandoffReason.QUOTE_UNAVAILABLE
                if result.kind in TECHNICAL_FAILURES
                else HandoffReason.INTEGRATION_ERROR
            ),
        )

        return update

    graph = StateGraph(ConversationState)

    graph.add_node("ingest", _ingest_node)
    graph.add_node("qualify", qualification_node)
    graph.add_node("load_catalog", load_catalog)
    graph.add_node("requalify", qualification_node)
    graph.add_node("quote", request_quote)
    graph.add_node("respond", _respond_node)
    graph.add_node("handoff", _handoff_node)

    graph.add_edge(START, "ingest")

    graph.add_conditional_edges(
        "ingest",
        _route_ingest,
        {
            "qualify": "qualify",
            "handoff": "handoff",
        },
    )

    graph.add_conditional_edges(
        "qualify",
        _route_qualification,
        {
            "respond": "respond",
            "load_catalog": "load_catalog",
            "quote": "quote",
            "handoff": "handoff",
        },
    )

    graph.add_edge("load_catalog", "requalify")

    graph.add_conditional_edges(
        "requalify",
        _route_after_catalog,
        {
            "respond": "respond",
            "quote": "quote",
            "handoff": "handoff",
        },
    )

    graph.add_conditional_edges(
        "quote",
        _route_after_quote,
        {
            "respond": "respond",
            "handoff": "handoff",
        },
    )

    graph.add_edge("respond", END)
    graph.add_edge("handoff", END)

    return graph.compile()


def run_turn(
    graph,
    state: ConversationState,
    *,
    patch: LeadPatch | None = None,
    request_handoff: bool = False,
) -> ConversationState:
    """Execute a turn with already-structured input.

    No persistence is implied. The caller retains the returned state.
    """
    return graph.invoke({
        **state,
        "pending_patch": patch,
        "handoff_requested": request_handoff,
    })
