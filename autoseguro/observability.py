
"""Structured operational audit and opt-in synthetic conversation evidence."""

import json
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from .conversation import ConversationTurn
from .state import ConversationState


AUDIT_FIELDS = frozenset({
    "conversation_id",
    "turn_id",
    "message_id",
    "reply_to_message_id",
    "role",
    "status",
    "elapsed_ms",
    "error_type",
    "next_action",
    "qualification_status",
    "missing_fields",
    "invalid_fields",
    "lead_fields_present",
    "quote_id",
    "quote_status",
    "quote_result_kind",
    "attempt_number",
    "http_status",
    "handoff_reason",
})


def _value(obj):
    if isinstance(obj, Enum):
        return obj.value
    return obj


class JsonlWriter:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, event: str, **fields) -> dict:
        record = {
            "schema_version": 1,
            "event_id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }

        self.path.parent.mkdir(parents=True, exist_ok=True)

        with self.path.open("a", encoding="utf-8") as file:
            file.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )

        return record

    def reset(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")


class TurnRecorder:
    """Record a real service turn, with optional synthetic-only evidence."""

    def __init__(
        self,
        audit_path: str | Path,
        evidence_path: str | Path | None = None,
    ):
        self.audit = JsonlWriter(audit_path)
        self.evidence = (
            JsonlWriter(evidence_path)
            if evidence_path is not None
            else None
        )

    def _audit(self, event: str, **fields) -> None:
        unknown = set(fields) - AUDIT_FIELDS
        if unknown:
            raise ValueError(
                f"Disallowed operational audit fields: {sorted(unknown)}"
            )

        self.audit.append(event, **fields)

    def _evidence(self, event: str, **fields) -> None:
        if self.evidence is not None:
            self.evidence.append(event, **fields)

    def reset_evidence(self) -> None:
        if self.evidence is None:
            raise RuntimeError("Evidence path is not configured")
        self.evidence.reset()

    def demo_status(
        self,
        status: str,
        *,
        reason_code: str | None = None,
        error_type: str | None = None,
    ) -> None:
        """No exception messages, secrets or arbitrary user text."""
        self._evidence(
            "demo_status",
            status=status,
            reason_code=reason_code,
            error_type=error_type,
        )

    def record_turn(
        self,
        service,
        state: ConversationState,
        message: str,
    ) -> ConversationTurn:
        conversation_id = state["conversation_id"]
        turn_id = str(uuid4())
        incoming_id = str(uuid4())
        started = time.perf_counter()

        common = {
            "conversation_id": conversation_id,
            "turn_id": turn_id,
        }

        self._audit(
            "message_status",
            **common,
            message_id=incoming_id,
            role="user",
            status="received",
        )
        self._evidence(
            "message",
            **common,
            message_id=incoming_id,
            role="user",
            status="received",
            content=message,
        )

        try:
            turn = service.handle(state, message)
        except Exception as exc:
            elapsed = round(
                (time.perf_counter() - started) * 1000
            )
            safe_type = type(exc).__name__

            self._audit(
                "message_status",
                **common,
                message_id=incoming_id,
                role="user",
                status="failed",
                elapsed_ms=elapsed,
                error_type=safe_type,
            )
            self._evidence(
                "turn_failed",
                **common,
                message_id=incoming_id,
                status="failed",
                elapsed_ms=elapsed,
                error_type=safe_type,
            )
            raise

        updated = turn.state
        elapsed = round((time.perf_counter() - started) * 1000)

        # A new quote_id means an actual quote operation occurred.
        new_quote = (
            updated["quote_id"] is not None
            and updated["quote_id"] != state["quote_id"]
        )

        if new_quote:
            quote_id = updated["quote_id"]

            for attempt in updated["quote_attempts"]:
                fields = {
                    **common,
                    "quote_id": quote_id,
                    "attempt_number": attempt.number,
                    "http_status": attempt.http_status,
                    "elapsed_ms": attempt.elapsed_ms,
                    "quote_result_kind": _value(attempt.kind),
                    "status": (
                        "succeeded"
                        if _value(attempt.kind) == "success"
                        else "failed"
                    ),
                }

                self._audit("quote_attempt", **fields)
                self._evidence("quote_attempt", **fields)

            result_fields = {
                **common,
                "quote_id": quote_id,
                "quote_status": _value(updated["quote_status"]),
                "quote_result_kind": (
                    _value(updated["quote_error_kind"])
                    if updated["quote_error_kind"] is not None
                    else "success"
                ),
            }

            self._audit("quote_result", **result_fields)

            # This detailed record is ONLY for explicitly enabled
            # synthetic demonstration evidence, never operational logs.
            if self.evidence is not None:
                request = updated["quote_request"]
                response = updated["quote_response"]

                self._evidence(
                    "quote_result",
                    **result_fields,
                    request=(
                        request.model_dump(mode="json")
                        if request is not None else None
                    ),
                    response=(
                        response.model_dump(mode="json")
                        if response is not None else None
                    ),
                    decline_reason=updated["decline_reason"],
                )

        qualification = updated["qualification"]

        decision = {
            **common,
            "next_action": _value(updated["next_action"]),
            "qualification_status": (
                _value(qualification.status)
                if qualification is not None else None
            ),
            "missing_fields": (
                list(qualification.missing_fields)
                if qualification is not None else []
            ),
            "invalid_fields": (
                list(qualification.invalid_fields)
                if qualification is not None else []
            ),
            "lead_fields_present": [
                field
                for field, value in updated["lead"]
                .model_dump().items()
                if value is not None
            ],
            "quote_id": updated["quote_id"],
            "quote_status": _value(updated["quote_status"]),
            "handoff_reason": _value(updated["handoff_reason"]),
        }

        self._audit("workflow_decision", **decision)
        self._evidence("workflow_decision", **decision)

        if (
            updated["handoff_reason"] is not None
            and (
                state["handoff_reason"] != updated["handoff_reason"]
            )
        ):
            self._audit(
                "handoff",
                **common,
                status="recorded_not_dispatched",
                handoff_reason=_value(updated["handoff_reason"]),
                quote_id=updated["quote_id"],
            )
            self._evidence(
                "handoff",
                **common,
                status="recorded_not_dispatched",
                handoff_reason=_value(updated["handoff_reason"]),
                quote_id=updated["quote_id"],
            )

        outgoing_id = str(uuid4())

        self._audit(
            "message_status",
            **common,
            message_id=incoming_id,
            role="user",
            status="processed",
            elapsed_ms=elapsed,
        )
        self._audit(
            "message_status",
            **common,
            message_id=outgoing_id,
            reply_to_message_id=incoming_id,
            role="assistant",
            status="generated",
        )

        self._evidence(
            "message",
            **common,
            message_id=outgoing_id,
            reply_to_message_id=incoming_id,
            role="assistant",
            status="generated",
            content=turn.text,
        )

        if self.evidence is not None:
            self._evidence(
                "state_snapshot",
                **common,
                quote_id=updated["quote_id"],
                quote_status=_value(updated["quote_status"]),
                next_action=_value(updated["next_action"]),
                handoff_reason=_value(updated["handoff_reason"]),
                quote_flow_active=updated["quote_flow_active"],
                lead=updated["lead"].model_dump(
                    mode="json", exclude_none=True
                ),
            )

        return turn
