
"""LangGraph-compatible state and quote invalidation."""

from enum import StrEnum
from typing import Annotated, TypedDict
from uuid import uuid4

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from .domain import LeadPatch, LeadProfile, merge_lead
from .qualification import QualificationResult, qualify
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
    OUT_OF_SCOPE = "out_of_scope"


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

    handoff_reason: HandoffReason | None


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
        handoff_reason=None,
    )


def apply_lead_patch(
    state: ConversationState,
    patch: LeadPatch,
) -> dict:
    """Return a state update without mutating the input."""

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
        )

    return update


def qualification_node(state: ConversationState) -> dict:
    return {
        "qualification": qualify(
            state["lead"],
            state["catalog"],
        )
    }
