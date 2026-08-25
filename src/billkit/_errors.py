"""Typed exception hierarchy mirroring the BillKit API error envelope.

The API returns errors in the Stripe-shape::

    {"error": {"type": "...", "code": "...", "message": "...", "param": "..."}}

Each ``type`` maps to one exception class so callers can ``except
RateLimitError`` instead of branching on HTTP status codes.
"""

from __future__ import annotations

from typing import Any


class BillKitError(Exception):
    """Base exception. Every error raised by the SDK is a subclass.

    Catch this when you want to handle all SDK errors uniformly
    (logging, retry, surfacing to the user) without branching on
    specific subtypes.
    """

    def __init__(
        self,
        message: str,
        *,
        type: str | None = None,
        code: str | None = None,
        param: str | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
        raw_body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.type = type
        self.code = code
        self.param = param
        self.status_code = status_code
        self.request_id = request_id
        self.raw_body = raw_body

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"message={self.message!r}, code={self.code!r}, "
            f"status_code={self.status_code!r}, request_id={self.request_id!r})"
        )


class APIConnectionError(BillKitError):
    """Network failure reaching the BillKit API. Always safe to retry."""


class APIError(BillKitError):
    """5xx from the API. Idempotency-Key makes retrying these safe."""


class ServerError(APIError):
    """Alias for 5xx errors with a clearer name when caught separately."""


class AuthenticationError(BillKitError):
    """401. The API key was missing, malformed, or revoked."""


class PermissionError(BillKitError):
    """403. The API key is valid but lacks the required scope."""


class ResourceMissingError(BillKitError):
    """404. The resource doesn't exist or doesn't belong to this tenant."""


class InvalidRequestError(BillKitError):
    """400 / 422. Parameter validation failed; check ``param`` for the field."""


class ConflictError(BillKitError):
    """409. Idempotency-key conflict, in-flight operation, or duplicate refund."""


class RateLimitError(BillKitError):
    """429. Slow down or honor ``retry_after``."""

    def __init__(self, *args: Any, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


_TYPE_TO_EXC: dict[str, type[BillKitError]] = {
    "api_connection_error": APIConnectionError,
    # ``api_error`` is the Stripe-convention type for 5xx, so surface it as
    # ``ServerError`` (a subclass of ``APIError``) so a caller can
    # ``except ServerError`` without false negatives.
    "api_error": ServerError,
    "authentication_error": AuthenticationError,
    "permission_error": PermissionError,
    "invalid_request_error": InvalidRequestError,
    "idempotency_error": ConflictError,
    "conflict": ConflictError,
    "rate_limit_error": RateLimitError,
}


def error_from_response(
    status_code: int,
    body: dict[str, Any] | None,
    *,
    request_id: str | None = None,
    retry_after: float | None = None,
) -> BillKitError:
    """Map an HTTP error response to the right exception subclass.

    Falls back to :class:`APIError` for 5xx and
    :class:`InvalidRequestError` for 4xx when the body doesn't follow
    the Stripe-shape envelope (e.g. a Traefik 502 HTML page).
    """
    error_obj = (body or {}).get("error") if isinstance(body, dict) else None
    if not isinstance(error_obj, dict):
        error_obj = {}

    err_type = error_obj.get("type") or _fallback_type(status_code)
    message = error_obj.get("message") or _fallback_message(status_code)
    code = error_obj.get("code")
    param = error_obj.get("param")

    exc_class = _TYPE_TO_EXC.get(err_type, _fallback_class(status_code))
    if status_code == 404 and exc_class is InvalidRequestError:
        exc_class = ResourceMissingError
    if status_code == 409 and exc_class is InvalidRequestError:
        exc_class = ConflictError

    kwargs: dict[str, Any] = {
        "type": err_type,
        "code": code,
        "param": param,
        "status_code": status_code,
        "request_id": request_id,
        "raw_body": body if isinstance(body, dict) else None,
    }
    if exc_class is RateLimitError:
        kwargs["retry_after"] = retry_after
    return exc_class(message, **kwargs)


def _fallback_type(status_code: int) -> str:
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "permission_error"
    if status_code == 404:
        return "invalid_request_error"
    if status_code == 409:
        return "conflict"
    if status_code == 429:
        return "rate_limit_error"
    if status_code >= 500:
        return "api_error"
    return "invalid_request_error"


def _fallback_message(status_code: int) -> str:
    return f"BillKit API returned HTTP {status_code} with no error body."


def _fallback_class(status_code: int) -> type[BillKitError]:
    if status_code == 401:
        return AuthenticationError
    if status_code == 403:
        return PermissionError
    if status_code == 404:
        return ResourceMissingError
    if status_code == 409:
        return ConflictError
    if status_code == 429:
        return RateLimitError
    if status_code >= 500:
        return ServerError
    return InvalidRequestError
