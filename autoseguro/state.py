
"""Conversation state, incremental updates, and quote invalidation."""

from enum import StrEnum
from typing import Annotated, TypedDict
from uuid import uuid4

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from .domain import LeadPatch, LeadProfile, merge_lead
from .qualification import QualificationResult, qualify
from .quote_client import QuoteAttempt, ResultKind
from .quote_contracts import (
    PlansResponse,
    QuoteRequest,
    QuoteResponse,
)


class QuoteStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    REQUESTED = "requested"
    SUCCEEDED = "succeeded"
    DECLINED = "declined"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


class HandoffReason(StrEnum):
    USER_REQUEST = "user_request"
    QUOTE_UNAVAILABLE = "quote_unavailable"
    CATALOG_UNAVAILABLE = "catalog_unavailable"
    INTEGRATION_ERROR = "integration_error"
    OUT_OF_SCOPE = "out_of_scope"


class TurnAction(StrEnum):
    ASK_INPUT = "ask_input"
    SHOW_QUOTE = "show_quote"
    EXPLAIN_DECLINE = "explain_decline"
    HANDOFF = "handoff"


class ConversationState(TypedDict):
    conversation_id: str
    messages: Annotated[list[AnyMessage], add_messages]

    lead: LeadProfile
    catalog: PlansResponse | None
    qualification: QualificationResult | None

    quote_id: str | None
    quote_request: QuoteRequest | None
    quote_response: QuoteResponse | None
    quote_status: QuoteStatus
    quote_attempts: tuple[QuoteAttempt, ...]
    quote_error_kind: ResultKind | None
    decline_reason: str | None

    handoff_reason: HandoffReason | None
    next_action: TurnAction | None

    pending_patch: LeadPatch | None
    handoff_requested: bool

    quote_flow_active: bool


def new_conversation_state() -> ConversationState:
    return ConversationState(
        conversation_id=str(uuid4()),
        messages=[],
        lead=LeadProfile(),
        catalog=None,
        qualification=None,
        quote_id=None,
        quote_request=None,
        quote_response=None,
        quote_status=QuoteStatus.NOT_REQUESTED,
        quote_attempts=(),
        quote_error_kind=None,
        decline_reason=None,
        handoff_reason=None,
        next_action=None,
        pending_patch=None,
        handoff_requested=False,
        quote_flow_active=False,
    )


def apply_lead_patch(
    state: ConversationState,
    patch: LeadPatch,
) -> dict:
    """Apply normalized changes without mutating the original state."""

    lead, changed = merge_lead(state["lead"], patch)

    if not changed:
        return {}

    update = {
        "lead": lead,
        "qualification": None,
    }

    if state["quote_status"] != QuoteStatus.NOT_REQUESTED:
        update.update(
            quote_id=None,
            quote_request=None,
            quote_response=None,
            quote_status=QuoteStatus.STALE,
            quote_attempts=(),
            quote_error_kind=None,
            decline_reason=None,
        )

    return update


def qualification_node(state: ConversationState) -> dict:
    """Pure qualification node; no HTTP calls or LLM."""
    return {
        "qualification": qualify(
            state["lead"],
            state["catalog"],
        )
    }
