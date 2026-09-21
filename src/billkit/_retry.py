"""Retry policy for transient failures.

Retries 5xx + network errors with jittered exponential backoff.
4xx are caller-fault and never retried, with one deliberate exception:
``409 idempotency_in_progress``. See :data:`IN_PROGRESS_CODE`. 429 is
retried only when the response advertises a ``Retry-After`` short
enough to be reasonable; otherwise we surface the exception so the
caller can decide.

The SDK auto-generates an ``Idempotency-Key`` for every mutating call
and reuses it across attempts, so retrying never double-charges: the
server replays the original response if the original handler completed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Idempotent-retry budget.

    ``max_attempts`` is the *total* attempt count including the
    initial try. ``max_retry_after`` caps how long we'll honor a
    server-supplied ``Retry-After`` header before surfacing the
    failure to the caller (so a misconfigured server can't pin a
    request for minutes).
    """

    max_attempts: int = 4
    initial_backoff_seconds: float = 0.5
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 8.0
    max_retry_after_seconds: float = 30.0
    jitter: float = 0.25  # ±25% of the computed wait

    def backoff_for(self, attempt: int) -> float:
        """Backoff before attempt ``attempt`` (1-indexed: attempt 2 is
        the first retry). Caller never asks for attempt=1."""
        base = self.initial_backoff_seconds * (self.backoff_multiplier ** (attempt - 2))
        capped = min(base, self.max_backoff_seconds)
        jitter_range = capped * self.jitter
        return max(0.0, capped + random.uniform(-jitter_range, jitter_range))


DEFAULT_RETRY_POLICY = RetryPolicy()

#: The one 409 error code that is transient rather than caller-fault.
#:
#: The server returns it when a request carrying the *same*
#: ``Idempotency-Key`` is still in flight ("Retry after a short delay",
#: ``Retry-After: 1``). It is the only 4xx where doing nothing is the
#: dangerous option: the call may well have charged the customer, the
#: caller cannot see the outcome, and the obvious workaround — retry with
#: a *fresh* key — is precisely what turns one charge into two.
#:
#: Retrying is safe because the transport reuses the original
#: ``Idempotency-Key`` on every attempt, so the retry either loses the
#: race again or replays the first call's recorded response.
IN_PROGRESS_CODE = "idempotency_in_progress"


def should_retry(
    status_code: int | None,
    *,
    attempt: int,
    policy: RetryPolicy,
    retry_after_seconds: float | None = None,
    error_code: str | None = None,
) -> bool:
    """Decide whether the next attempt is allowed.

    ``status_code=None`` means a transport-level failure (network error,
    DNS, TLS, timeout), so always retry within budget. Any 5xx is a
    server fault, also retry. A 429 is retried only when the server
    supplies a short, parseable ``Retry-After`` value; this keeps
    tenant-side workers from sleeping for unbounded periods.

    ``error_code`` is the envelope's ``error.code``, and is consulted for
    409s only; every other decision is status-driven.
    """
    if attempt >= policy.max_attempts:
        return False
    if status_code is None:
        return True
    if status_code == 409:
        # A 409 from a *different* code (``idempotency_key_in_use``, a
        # conflicting subscription state) is a genuine caller-fault
        # conflict that retrying can only repeat, so it still fails fast.
        return error_code == IN_PROGRESS_CODE
    if status_code == 429:
        return (
            retry_after_seconds is not None
            and 0 <= retry_after_seconds <= policy.max_retry_after_seconds
        )
    return status_code >= 500
