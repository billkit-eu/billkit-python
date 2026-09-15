"""Webhook signature verification: valid, replay, tamper, malformed."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from billkit import WebhookSignature, WebhookVerificationError


def _sign(body: bytes, secret: str, *, ts: int | None = None) -> str:
    timestamp = ts if ts is not None else int(time.time())
    signed = f"{timestamp}.".encode("ascii") + body
    v1 = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={v1}"


def test_valid_signature_returns_decoded_event(
    sample_event_body: bytes, webhook_secret: str
) -> None:
    header = _sign(sample_event_body, webhook_secret)
    event = WebhookSignature.verify(
        payload=sample_event_body,
        signature_header=header,
        secret=webhook_secret,
    )
    assert event["id"] == "evt_1"
    assert event["type"] == "customer.created"


def test_str_payload_accepted(sample_event_body: bytes, webhook_secret: str) -> None:
    header = _sign(sample_event_body, webhook_secret)
    event = WebhookSignature.verify(
        payload=sample_event_body.decode("utf-8"),
        signature_header=header,
        secret=webhook_secret,
    )
    assert event["id"] == "evt_1"


def test_replay_outside_tolerance_rejected(sample_event_body: bytes, webhook_secret: str) -> None:
    stale_header = _sign(sample_event_body, webhook_secret, ts=int(time.time()) - 600)
    with pytest.raises(WebhookVerificationError, match="tolerance"):
        WebhookSignature.verify(
            payload=sample_event_body,
            signature_header=stale_header,
            secret=webhook_secret,
        )


def test_tampered_body_rejected(sample_event_body: bytes, webhook_secret: str) -> None:
    header = _sign(sample_event_body, webhook_secret)
    tampered = sample_event_body.replace(b"cus_1", b"cus_9")
    with pytest.raises(WebhookVerificationError, match="mismatch"):
        WebhookSignature.verify(
            payload=tampered,
            signature_header=header,
            secret=webhook_secret,
        )


def test_wrong_secret_rejected(sample_event_body: bytes, webhook_secret: str) -> None:
    header = _sign(sample_event_body, webhook_secret)
    with pytest.raises(WebhookVerificationError, match="mismatch"):
        WebhookSignature.verify(
            payload=sample_event_body,
            signature_header=header,
            secret="whsec_wrong",
        )


def test_missing_header_rejected(sample_event_body: bytes, webhook_secret: str) -> None:
    with pytest.raises(WebhookVerificationError, match="Missing"):
        WebhookSignature.verify(
            payload=sample_event_body,
            signature_header=None,
            secret=webhook_secret,
        )


def test_malformed_header_rejected(sample_event_body: bytes, webhook_secret: str) -> None:
    with pytest.raises(WebhookVerificationError, match="Malformed"):
        WebhookSignature.verify(
            payload=sample_event_body,
            signature_header="not-a-real-header",
            secret=webhook_secret,
        )


def test_malformed_v1_hex_rejected(sample_event_body: bytes, webhook_secret: str) -> None:
    header = f"t={int(time.time())},v1={'z' * 64}"
    with pytest.raises(WebhookVerificationError, match="Malformed"):
        WebhookSignature.verify(
            payload=sample_event_body,
            signature_header=header,
            secret=webhook_secret,
        )


def test_non_json_body_rejected_after_signature_passes(
    webhook_secret: str,
) -> None:
    body = b"<html>not json</html>"
    header = _sign(body, webhook_secret)
    with pytest.raises(WebhookVerificationError, match="JSON"):
        WebhookSignature.verify(payload=body, signature_header=header, secret=webhook_secret)


def test_accepts_when_any_of_multiple_v1_matches(
    sample_event_body: bytes, webhook_secret: str
) -> None:
    # Rotation shape: an old (wrong) signature alongside the current one.
    ts = int(time.time())
    signed = f"{ts}.".encode("ascii") + sample_event_body
    good = hmac.new(webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
    bad = hmac.new(b"whsec_rotated_out", signed, hashlib.sha256).hexdigest()
    header = f"t={ts},v1={bad},v1={good}"

    event = WebhookSignature.verify(
        payload=sample_event_body,
        signature_header=header,
        secret=webhook_secret,
    )
    assert event["id"] == "evt_1"


def test_rejects_when_none_of_multiple_v1_match(
    sample_event_body: bytes, webhook_secret: str
) -> None:
    ts = int(time.time())
    signed = f"{ts}.".encode("ascii") + sample_event_body
    bad1 = hmac.new(b"whsec_wrong_a", signed, hashlib.sha256).hexdigest()
    bad2 = hmac.new(b"whsec_wrong_b", signed, hashlib.sha256).hexdigest()
    header = f"t={ts},v1={bad1},v1={bad2}"

    with pytest.raises(WebhookVerificationError, match="mismatch"):
        WebhookSignature.verify(
            payload=sample_event_body,
            signature_header=header,
            secret=webhook_secret,
        )
