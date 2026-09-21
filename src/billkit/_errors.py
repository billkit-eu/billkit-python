"""Typed exception hierarchy mirroring the BillKit API error envelope.

The API returns errors in the Stripe-shape::

    {"error": {"type": "...", "code": "...", "message": "...", "param": "..."}}

The HTTP **status** picks the class, so callers can ``except
RateLimitError`` instead of branching on status codes themselves. The
envelope's ``type``/``code``/``param`` ride along on the raised object.
See :func:`_class_for_status` for why the status, and not ``type``, is
the authority.
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


def _class_for_status(status_code: int) -> type[BillKitError]:
    """Pick the exception class from the HTTP **status**, not the envelope
    ``type``.

    The status is the field the API cannot get wrong. ``type`` is accurate
    for errors BillKit raises itself, but a request that never reaches a
    route handler — an unmatched path, a method the route does not allow —
    is serialised by the framework-level handler as
    ``{"type": "api_error", "code": "unhandled"}`` *with a 4xx status*.
    Trusting ``type`` there mapped a plain ``404 Not Found`` (a typo in a
    resource id, or an SDK/API version skew) onto :class:`ServerError`,
    telling the caller BillKit had broken when their own request was at
    fault — and ``ServerError`` is the class retry and alerting policies
    key on.

    The envelope ``type`` is still preserved verbatim on
    :attr:`BillKitError.type`; only the class is status-driven. The node
    and php clients decide this the same way.
    """
    if status_code >= 500:
        return ServerError
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
    # Everything else below 500 (400, 405, 422, 451 ...) is a request the
    # caller has to change.
    return InvalidRequestError


def error_from_response(
    status_code: int,
    body: dict[str, Any] | None,
    *,
    request_id: str | None = None,
    retry_after: float | None = None,
) -> BillKitError:
    """Map an HTTP error response to the right exception subclass.

    A body that doesn't follow the Stripe-shape envelope (a Traefik 502
    HTML page, an empty 404) still produces a typed error, because the
    status alone is enough to choose one.
    """
    error_obj = (body or {}).get("error") if isinstance(body, dict) else None
    if not isinstance(error_obj, dict):
        error_obj = {}

    err_type = error_obj.get("type") or _fallback_type(status_code)
    message = error_obj.get("message") or (
        f"BillKit API returned HTTP {status_code} with no error body."
    )

    exc_class = _class_for_status(status_code)
    kwargs: dict[str, Any] = {
        "type": err_type,
        "code": error_obj.get("code"),
        "param": error_obj.get("param"),
        "status_code": status_code,
        "request_id": request_id,
        "raw_body": body if isinstance(body, dict) else None,
    }
    if exc_class is RateLimitError:
        kwargs["retry_after"] = retry_after
    return exc_class(message, **kwargs)


def _fallback_type(status_code: int) -> str:
    """The ``type`` the API would have sent, for a response that carried no
    envelope. Only fills :attr:`BillKitError.type`; it never picks the class."""
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
