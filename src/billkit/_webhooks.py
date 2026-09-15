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
delivery.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any

DEFAULT_TOLERANCE_SECONDS = 300
_V1_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class WebhookVerificationError(Exception):
    """Signature missing, malformed, stale, or did not match."""


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
    ) -> dict[str, Any]:
        """Verify the signature + parse the JSON body.

        Returns the decoded event dict on success. Raises
        :class:`WebhookVerificationError` on any failure mode.
        """
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
            return json.loads(payload_bytes)  # type: ignore[no-any-return]
        except json.JSONDecodeError as exc:
            raise WebhookVerificationError(f"Webhook body is not valid JSON: {exc}") from exc


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
    try:
        ts = int(ts_raw)
    except ValueError as exc:
        raise WebhookVerificationError(
            f"Malformed BillKit-Signature timestamp: {ts_raw!r}"
        ) from exc
    if ts <= 0:
        raise WebhookVerificationError(f"Malformed BillKit-Signature timestamp: {ts!r}")
    valid = [v for v in v1_values if _V1_HEX_RE.fullmatch(v) is not None]
    if not valid:
        raise WebhookVerificationError(f"Malformed BillKit-Signature v1 signature: {v1_values!r}")
    return ts, valid
