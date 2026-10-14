"""Per-request options and the ``NOT_GIVEN`` sentinel.

Every resource method takes the same keyword-only options, after its own
parameters:

``idempotency_key``
    Mutating methods only. Your own ``Idempotency-Key`` for this call. The
    SDK sends a generated one when you do not, and either way the key is
    fixed once per call, before the retry loop, so every attempt carries
    the same one.
``timeout``
    Seconds (``float``) or an ``httpx.Timeout`` for this call, overriding
    the client's. ``None`` disables the timeout.
``max_retries``
    Retries for this call (``0`` sends exactly one attempt), overriding the
    client's :class:`billkit.RetryPolicy`. Which failures are retried does
    not change.
``extra_headers``
    Headers added to this call, matched case-insensitively. ``Authorization``,
    ``Idempotency-Key``, ``Content-Type`` and ``User-Agent`` belong to the
    SDK and raise :class:`ValueError`; use ``idempotency_key=`` for the key.
``extra_query``
    Query parameters added to this call, for a filter or ``expand`` the
    API supports and this SDK version does not have a keyword for yet.
    Merged over the SDK's own, so a key here replaces the keyword's value.
    A ``None`` value is dropped; a list filter is a comma-joined string,
    as the API reads it.
``extra_body``
    ``POST`` methods only. Top-level JSON body fields added to this call,
    merged over the ones the SDK built from its keywords. Unlike the
    keywords, a ``None`` value here is sent as a JSON ``null``.

``extra_query`` and ``extra_body`` are the escape hatch for an API field
newer than the SDK. The SDK does not check them; a field the route does
not take is the API's ``400 parameter_unknown``, naming it in ``param``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

import httpx

__all__ = ["NOT_GIVEN", "NotGiven", "RequestOptions"]


class NotGiven:
    """The type of :data:`NOT_GIVEN`: "this keyword was not passed".

    Used where ``None`` is a meaningful value and so cannot also mean
    "leave it alone": an update field that the API clears when sent as
    ``null`` (``customers.update(name=None)`` clears the name, omitting
    ``name`` leaves it), and ``timeout=None`` (no timeout at all). Falsy,
    so ``if value:`` treats it like ``None``; test with
    ``isinstance(value, NotGiven)`` when the difference matters.
    """

    _instance: NotGiven | None = None

    def __new__(cls) -> NotGiven:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> Literal[False]:
        return False

    def __repr__(self) -> str:
        return "NOT_GIVEN"

    def __reduce__(self) -> str:
        return "NOT_GIVEN"


#: The default of every keyword where ``None`` already means something.
NOT_GIVEN: Final = NotGiven()


@dataclass(frozen=True, slots=True)
class RequestOptions:
    """The per-call options, as the transport receives them."""

    idempotency_key: str | None = None
    timeout: float | httpx.Timeout | NotGiven | None = NOT_GIVEN
    max_retries: int | NotGiven = NOT_GIVEN
    extra_headers: Mapping[str, str] | None = None
    extra_query: Mapping[str, Any] | None = None
    extra_body: Mapping[str, Any] | None = None


DEFAULT_OPTIONS: Final = RequestOptions()
