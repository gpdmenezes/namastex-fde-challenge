
"""Typed HTTP client for the local quote API, with bounded retries."""

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable
from uuid import uuid4

import httpx
from pydantic import ValidationError

from .quote_contracts import (
    CommercialDecline,
    HealthResponse,
    InvalidPayload,
    PlansResponse,
    QuoteRequest,
    QuoteResponse,
    SchemaValidationError,
)


class ResultKind(StrEnum):
    SUCCESS = "success"
    COMMERCIAL_DECLINE = "commercial_decline"
    SCHEMA_VALIDATION = "schema_validation"
    INVALID_PAYLOAD = "invalid_payload"
    TRANSIENT_ERROR = "transient_error"
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    INVALID_RESPONSE = "invalid_response"
    UNEXPECTED_HTTP = "unexpected_http"


RETRYABLE = frozenset({
    ResultKind.TRANSIENT_ERROR,
    ResultKind.TIMEOUT,
    ResultKind.TRANSPORT_ERROR,
})

TRANSIENT_STATUSES = frozenset({500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 0.25
    connect_timeout: float = 1.0
    read_timeout: float = 2.5
    write_timeout: float = 2.0
    pool_timeout: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be >= 0")
        if any(t <= 0 for t in (
            self.connect_timeout,
            self.read_timeout,
            self.write_timeout,
            self.pool_timeout,
        )):
            raise ValueError("timeouts must be > 0")

    def httpx_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=self.write_timeout,
            pool=self.pool_timeout,
        )


@dataclass(frozen=True)
class QuoteAttempt:
    number: int
    http_status: int | None
    elapsed_ms: int
    kind: ResultKind


@dataclass(frozen=True)
class QuoteResult:
    quote_id: str
    kind: ResultKind
    quote: QuoteResponse | None
    attempts: tuple[QuoteAttempt, ...]
    total_elapsed_ms: int
    decline_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.kind == ResultKind.SUCCESS and self.quote is not None


class QuoteClientError(Exception):
    """Safe error for non-quote endpoints; never embeds response bodies."""

    def __init__(self, kind: ResultKind, http_status: int | None = None):
        self.kind = kind
        self.http_status = http_status
        super().__init__(f"{kind.value} (HTTP {http_status})")


def _object(response: httpx.Response) -> dict | None:
    try:
        obj = response.json()
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _classify_quote(
    response: httpx.Response,
) -> tuple[ResultKind, QuoteResponse | None, str | None]:
    status = response.status_code

    if status in TRANSIENT_STATUSES:
        return ResultKind.TRANSIENT_ERROR, None, None

    data = _object(response)

    if status == 200:
        try:
            if data is None:
                raise ValueError("Expected JSON object")
            return ResultKind.SUCCESS, QuoteResponse.model_validate(data), None
        except (ValidationError, ValueError):
            return ResultKind.INVALID_RESPONSE, None, None

    if status == 422:
        if data is not None and data.get("error") == "cotacao_recusada":
            try:
                decline = CommercialDecline.model_validate(data)
            except ValidationError:
                return ResultKind.INVALID_RESPONSE, None, None
            return ResultKind.COMMERCIAL_DECLINE, None, decline.motivo

        if data is not None and isinstance(data.get("detail"), list):
            try:
                SchemaValidationError.model_validate(data)
            except ValidationError:
                return ResultKind.INVALID_RESPONSE, None, None
            return ResultKind.SCHEMA_VALIDATION, None, None

        return ResultKind.UNEXPECTED_HTTP, None, None

    if status == 400:
        try:
            if data is None:
                raise ValueError("Expected JSON object")
            InvalidPayload.model_validate(data)
        except (ValidationError, ValueError):
            return ResultKind.UNEXPECTED_HTTP, None, None
        return ResultKind.INVALID_PAYLOAD, None, None

    return ResultKind.UNEXPECTED_HTTP, None, None


class QuoteClient:
    """Synchronous integration boundary; POST retries are safe for this mock."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        policy: RetryPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.policy = policy or RetryPolicy()
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url,
            timeout=self.policy.httpx_timeout(),
            transport=transport,
            trust_env=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "QuoteClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, path: str, model: type[HealthResponse] | type[PlansResponse]):
        try:
            response = self._http.get(path)
        except httpx.TimeoutException as exc:
            raise QuoteClientError(ResultKind.TIMEOUT) from exc
        except httpx.TransportError as exc:
            raise QuoteClientError(ResultKind.TRANSPORT_ERROR) from exc

        if response.status_code != 200:
            kind = (
                ResultKind.TRANSIENT_ERROR
                if response.status_code in TRANSIENT_STATUSES
                else ResultKind.UNEXPECTED_HTTP
            )
            raise QuoteClientError(kind, response.status_code)

        data = _object(response)
        try:
            if data is None:
                raise ValueError("Expected JSON object")
            return model.model_validate(data)
        except (ValidationError, ValueError) as exc:
            raise QuoteClientError(ResultKind.INVALID_RESPONSE, 200) from exc

    def get_health(self) -> HealthResponse:
        return self._get("/health", HealthResponse)

    def get_plans(self) -> PlansResponse:
        return self._get("/planos", PlansResponse)

    def quote(self, request: QuoteRequest) -> QuoteResult:
        started_total = time.perf_counter()
        quote_id = str(uuid4())
        payload = request.model_dump(mode="json", exclude_none=True)

        attempts: list[QuoteAttempt] = []
        kind = ResultKind.TRANSPORT_ERROR
        quote: QuoteResponse | None = None
        reason: str | None = None

        for number in range(1, self.policy.max_attempts + 1):
            started = time.perf_counter()
            status: int | None = None

            try:
                response = self._http.post("/quote", json=payload)
            except httpx.TimeoutException:
                kind = ResultKind.TIMEOUT
            except httpx.TransportError:
                kind = ResultKind.TRANSPORT_ERROR
            else:
                status = response.status_code
                kind, quote, reason = _classify_quote(response)

            attempts.append(QuoteAttempt(
                number=number,
                http_status=status,
                elapsed_ms=round(
                    (time.perf_counter() - started) * 1000
                ),
                kind=kind,
            ))

            if kind not in RETRYABLE or number == self.policy.max_attempts:
                break

            self._sleep(
                self.policy.backoff_seconds * (2 ** (number - 1))
            )

        return QuoteResult(
            quote_id=quote_id,
            kind=kind,
            quote=quote,
            attempts=tuple(attempts),
            total_elapsed_ms=round(
                (time.perf_counter() - started_total) * 1000
            ),
            decline_reason=reason,
        )
