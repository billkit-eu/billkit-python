"""The exception hierarchy. The same names mean the same thing in every BillKit SDK.

.. code-block:: text

    BillKitError                    every error a request (or a webhook check) raises
    ├── APIError                    the API answered with an HTTP error
    │   ├── InvalidRequestError     400 and any other 4xx not below
    │   ├── AuthenticationError     401
    │   ├── PaymentError            402
    │   ├── PermissionDeniedError   403
    │   ├── NotFoundError           404
    │   ├── ConflictError           409
    │   ├── RateLimitError          429
    │   └── ServerError             5xx
    ├── APIConnectionError          no answer: network, DNS, TLS
    │   └── APITimeoutError         no answer in time
    └── WebhookVerificationError    a delivery failed verification

The API's errors are Stripe-shaped::

    {"error": {"type": "...", "code": "...", "message": "...", "param": "...", "reason": "..."}}

The HTTP **status** picks the class; see :func:`_class_for_status` for why
the status, and not ``type``, is the authority. ``type``, ``code``,
``param`` and ``reason`` ride along on the raised object.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

__all__ = [
    "APIConnectionError",
    "APIError",
    "APITimeoutError",
    "AuthenticationError",
    "BillKitError",
    "ConflictError",
    "InvalidRequestError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "ServerError",
    "WebhookVerificationError",
]


class BillKitError(Exception):
    """Base class of every error the SDK raises.

    Catch it to handle every SDK failure in one place. The HTTP attributes
    are declared here so a handler typed against the base class can read
    them; they are ``None`` unless the error is an :class:`APIError`.

    ``reason`` is the finer-grained cause some codes carry. A ``409
    operation_pending`` from cancelling, pausing or changing the plan of a
    subscription whose renewal payment is still settling carries
    ``"renewal_processing"`` (the message names when to retry), and a
    blank ``Idempotency-Key`` is a ``400 parameter_invalid`` with
    ``"idempotency_key_blank"``. Known values are in
    :class:`billkit.types.ErrorReason`; the set is open.
    """

    message: str
    status_code: int | None = None
    type: str | None = None
    code: str | None = None
    param: str | None = None
    reason: str | None = None
    request_id: str | None = None
    raw_body: dict[str, Any] | str | None = None
    headers: Mapping[str, str] | None = None

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __repr__(self) -> str:
        return f"{type(self).__name__}(message={self.message!r})"

    def __reduce__(self) -> tuple[Any, ...]:
        # The default exception pickle calls ``cls(*self.args)``, which
        # cannot satisfy ``APIError``'s keyword-only ``status_code`` (or
        # any subclass's own signature), so an error crossing a process
        # boundary (multiprocessing, Celery, a cache) failed to unpickle.
        # Rebuild without calling ``__init__`` and restore every attribute.
        return (_restore_error, (type(self), self.args, dict(self.__dict__)))


class APIError(BillKitError):
    """The API answered, with an HTTP error status.

    Every attribute is populated from the response: ``status_code``, the
    envelope's ``type``, ``code``, ``param``, ``reason`` and ``message``,
    the ``request_id`` (quote it in a support request), ``raw_body`` (the
    decoded JSON object, the body text when it was not a JSON object, or
    ``None`` when there was no body) and the response ``headers``.

    A 2xx whose body is not JSON is raised as a plain :class:`APIError`
    too, with that status and the text on ``raw_body``: it is not a
    response the SDK can hand back.

    In 0.x this name meant "a 5xx". It is now the parent of every HTTP
    error; catch :class:`ServerError` for the 5xx case.
    """

    status_code: int

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        type: str | None = None,
        code: str | None = None,
        param: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
        raw_body: dict[str, Any] | str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.type = type
        self.code = code
        self.param = param
        self.reason = reason
        self.request_id = request_id
        self.raw_body = raw_body
        self.headers = headers

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"message={self.message!r}, status_code={self.status_code!r}, "
            f"code={self.code!r}, request_id={self.request_id!r})"
        )


class InvalidRequestError(APIError):
    """400, or another 4xx with no class of its own. ``param`` names the field."""


class AuthenticationError(APIError):
    """401. The API key was missing, malformed or revoked."""


class PaymentError(APIError):
    """402. A charge was refused, or the account is suspended.

    ``code`` says which: ``payment_refused`` when the payment provider
    declined the charge (a refund the provider would not make, say), with
    the provider's own reason in ``message``; ``tenant_suspended`` when the
    BillKit account behind the key is suspended and the call would move
    money. Neither is retried: the same request gets the same answer until
    something outside it changes.
    """


class PermissionDeniedError(APIError):
    """403. The key is valid but its scopes do not cover the route.

    Replaces 0.x ``PermissionError``, which shadowed the builtin.
    """


class NotFoundError(APIError):
    """404. The object does not exist, or belongs to another account.

    Cross-account access is a 404 rather than a 403 on purpose, so probing
    cannot confirm that an id exists. Replaces 0.x ``ResourceMissingError``.
    """


class ConflictError(APIError):
    """409. An idempotency conflict or a state clash; ``code`` says which.

    ``idempotency_in_progress`` is retried by the SDK before you see it;
    ``idempotency_key_in_use`` (same key, different body) and the state
    clashes are not, because retrying can only repeat them.
    """


class RateLimitError(APIError):
    """429. ``retry_after`` is the server's ``Retry-After`` in seconds, when it sent one."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ServerError(APIError):
    """5xx. The SDK has already retried it within the retry budget, except
    a 501, which is a permanent answer and is raised on the first attempt."""


class APIConnectionError(BillKitError):
    """No HTTP answer: network, DNS or TLS failure. Already retried within the budget.

    Whether a mutating request took effect is unknown. Retry it with the
    same ``idempotency_key`` and the API replays the first result if there
    was one.
    """


class APITimeoutError(APIConnectionError):
    """No answer within the timeout. Already retried within the budget."""


class WebhookVerificationError(BillKitError):
    """A webhook delivery's signature was missing, malformed, stale or wrong,
    or its body was not a JSON object. Answer the delivery with a 400."""


def _restore_error(
    cls: type[BillKitError], args: tuple[Any, ...], state: dict[str, Any]
) -> BillKitError:
    """Unpickle a :class:`BillKitError` (see :meth:`BillKitError.__reduce__`)."""
    error = cls.__new__(cls)
    error.args = args
    error.__dict__.update(state)
    return error


def _class_for_status(status_code: int) -> type[APIError]:
    """Pick the exception class from the HTTP **status**, not the envelope
    ``type``.

    The status is the field nothing in between can get wrong. An unmatched
    route is a ``404`` with ``type`` ``invalid_request_error`` and ``code``
    ``route_not_found``, which is a :class:`NotFoundError` here, not an
    :class:`InvalidRequestError`. Before 1.0 the same miss read
    ``{"type": "api_error"}`` *with a 4xx status*, and trusting ``type``
    mapped a plain ``404`` (a typo in an id, or an SDK/API version skew) onto
    :class:`ServerError`, the class retry and alerting policies key on; a
    proxy in front of the API can still answer that way.

    The envelope ``type`` is still preserved verbatim on
    :attr:`APIError.type`; only the class is status-driven. The node and
    php clients decide this the same way.
    """
    if status_code >= 500:
        return ServerError
    if status_code == 401:
        return AuthenticationError
    if status_code == 402:
        return PaymentError
    if status_code == 403:
        return PermissionDeniedError
    if status_code == 404:
        return NotFoundError
    if status_code == 409:
        return ConflictError
    if status_code == 429:
        return RateLimitError
    # Everything else below 500 (400, 405, 413 ...) is a request the
    # caller has to change.
    return InvalidRequestError


def error_from_response(
    status_code: int,
    body: Any,
    *,
    request_id: str | None = None,
    retry_after: float | None = None,
    headers: httpx.Headers | Mapping[str, str] | None = None,
    raw_text: str | None = None,
) -> APIError:
    """Map an HTTP error response to the right exception subclass.

    A body that does not follow the envelope (a proxy's HTML 502, an empty
    404) still produces a typed error, because the status alone is enough
    to choose one. ``raw_text`` is the undecoded body; when ``body`` is not
    a JSON object it becomes ``raw_body``, so the proxy's page is not lost.
    """
    error_obj = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error_obj, dict):
        error_obj = {}

    def text(key: str) -> str | None:
        value = error_obj.get(key)
        return value if isinstance(value, str) else None

    message = text("message") or f"BillKit API returned HTTP {status_code} with no error body."
    exc_class = _class_for_status(status_code)
    kwargs: dict[str, Any] = {
        "status_code": status_code,
        "type": text("type") or _fallback_type(status_code),
        "code": text("code"),
        "param": text("param"),
        "reason": text("reason"),
        "request_id": request_id,
        "raw_body": body if isinstance(body, dict) else (raw_text or None),
        "headers": headers,
    }
    if exc_class is RateLimitError:
        return RateLimitError(message, retry_after=retry_after, **kwargs)
    return exc_class(message, **kwargs)


def _fallback_type(status_code: int) -> str:
    """The ``type`` the API would have sent, for a response with no envelope.
    Only fills :attr:`APIError.type`; it never picks the class."""
    if status_code == 401:
        return "authentication_error"
    if status_code == 402:
        return "payment_error"
    if status_code == 403:
        return "permission_error"
    if status_code == 404:
        return "not_found_error"
    if status_code == 409:
        return "conflict_error"
    if status_code == 429:
        return "rate_limit_error"
    if status_code >= 500:
        return "api_error"
    return "invalid_request_error"
