"""Verify ``BillKit-Signature: t=<unix>,v1=<hex>`` headers.

The server computes ``HMAC_SHA256(secret, f"{t}.{payload}")``. The
verifier:

1. Parses the header (rejects malformed shapes). A header may carry
   more than one ``v1=`` value, because the server emits both the old and the
   new signature while a signing secret is being rotated, and
   verification passes if **any** of them matches.
2. Confirms the timestamp is within :data:`DEFAULT_TOLERANCE_SECONDS`
   of now (replay protection).
3. Computes the expected HMAC and compares against each candidate in
   constant time.

If any step fails, :class:`WebhookVerificationError` is raised. Catch
that one exception type and return 400 to BillKit so it retries the
delivery. On success the body is returned as a typed event; see
:meth:`WebhookSignature.verify`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time

from billkit._errors import WebhookVerificationError
from billkit._models import construct
from billkit.types import Event

__all__ = ["DEFAULT_TOLERANCE_SECONDS", "WebhookSignature", "WebhookVerificationError"]

DEFAULT_TOLERANCE_SECONDS = 300

_V1_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")

#: ASCII digits only, and few enough of them to be a Unix time in seconds.
#: ``int()`` alone accepted ``+``, ``_``, surrounding space and non-ASCII
#: digits, and a few hundred digits parsed fine and then raised
#: ``OverflowError`` (not :class:`WebhookVerificationError`) in the
#: tolerance check. Twelve digits reach the year 33658.
_TIMESTAMP_RE = re.compile(r"[0-9]{1,12}", re.ASCII)


class WebhookSignature:
    """Static helpers for verifying inbound webhooks.

    Stateless by design: instantiate nothing, just call
    :meth:`verify`. The class is the namespace.
    """

    @staticmethod
    def verify(
        *,
        payload: bytes | str,
        signature_header: str | None,
        secret: str,
        tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    ) -> Event:
        """Verify the signature, then parse the body into an :class:`~billkit.Event`.

        Returns the specific event class when this SDK version knows the
        ``type`` (``SubscriptionUpdatedEvent``, ``InvoicePaidEvent``, ...,
        each with a typed ``data``), and a plain :class:`~billkit.Event`
        otherwise. An unknown type is never an error: ignore it and answer
        2xx, or BillKit retries a delivery you were never going to process.
        ``event.previous_attributes`` is always present and ``None`` when
        nothing changed. ``event.to_dict()`` gives the plain JSON.

        Raises :class:`WebhookVerificationError` when the header is missing,
        malformed or stale, no signature matches, or the body is not a JSON
        object. Raises :class:`ValueError` when ``secret`` is empty or
        whitespace, or ``tolerance_seconds`` is not a finite number 0 or
        more: that is a configuration mistake (an unset environment
        variable, usually), not a bad delivery, and an empty HMAC key is one
        anybody can sign with.
        """
        if not isinstance(secret, str) or not secret.strip():
            raise ValueError(
                "secret must be the endpoint's signing secret (bkwhsec_...); "
                "got an empty value. Check the variable it is read from."
            )
        # NaN makes every comparison false, so it would accept a replay of
        # any age; infinity says so outright. Both, and a negative window,
        # are configuration faults rather than bad deliveries.
        if not math.isfinite(tolerance_seconds) or tolerance_seconds < 0:
            raise ValueError(
                "tolerance_seconds must be a finite number of seconds, 0 or more; "
                f"got {tolerance_seconds!r}."
            )
        if signature_header is None:
            raise WebhookVerificationError("Missing BillKit-Signature header.")

        ts, v1_candidates = _parse_signature_header(signature_header)
        if abs(time.time() - ts) > tolerance_seconds:
            raise WebhookVerificationError(
                f"Signature timestamp outside ±{tolerance_seconds}s tolerance."
            )

        payload_bytes = payload.encode("utf-8") if isinstance(payload, str) else payload

        signed = f"{ts}.".encode("ascii") + payload_bytes
        expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        # Compare every candidate rather than short-circuiting on the first
        # match, so the loop's timing doesn't reveal *which* signature of a
        # rotation pair matched. Matches the node + php verifiers.
        #
        # ``.lower()`` matters: ``_V1_HEX_RE`` deliberately accepts
        # upper-case hex (a conforming sender may emit it), but
        # ``hexdigest()`` is always lower-case, so a case-sensitive compare
        # would reject an otherwise valid upper-case signature outright.
        matched = False
        for v1 in v1_candidates:
            if hmac.compare_digest(expected, v1.lower()):
                matched = True
        if not matched:
            raise WebhookVerificationError("Signature mismatch.")

        try:
            body = json.loads(payload_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WebhookVerificationError(f"Webhook body is not valid JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise WebhookVerificationError(
                f"Webhook body is a JSON {type(body).__name__}, not an event object."
            )
        return construct(Event, body)


def _parse_signature_header(header: str) -> tuple[int, list[str]]:
    ts_raw: str | None = None
    v1_values: list[str] = []
    for chunk in header.split(","):
        if "=" not in chunk:
            continue
        key, _, value = chunk.strip().partition("=")
        key = key.strip()
        value = value.strip()
        if key == "t":
            ts_raw = value
        elif key == "v1":
            v1_values.append(value)
    if ts_raw is None or not v1_values:
        raise WebhookVerificationError(f"Malformed BillKit-Signature header: {header!r}")
    if _TIMESTAMP_RE.fullmatch(ts_raw) is None:
        raise WebhookVerificationError(f"Malformed BillKit-Signature timestamp: {ts_raw[:32]!r}")
    ts = int(ts_raw)
    if ts <= 0:
        raise WebhookVerificationError(f"Malformed BillKit-Signature timestamp: {ts!r}")
    valid = [v for v in v1_values if _V1_HEX_RE.fullmatch(v) is not None]
    if not valid:
        raise WebhookVerificationError(f"Malformed BillKit-Signature v1 signature: {v1_values!r}")
    return ts, valid
