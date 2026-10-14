"""Webhook signature verification: valid, replay, tamper, malformed."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from billkit import BillKitError, Event, WebhookSignature, WebhookVerificationError
from billkit.types import Subscription, SubscriptionUpdatedEvent


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
            secret="bkwhsec_wrong",
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
    bad = hmac.new(b"bkwhsec_rotated_out", signed, hashlib.sha256).hexdigest()
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
    bad1 = hmac.new(b"bkwhsec_wrong_a", signed, hashlib.sha256).hexdigest()
    bad2 = hmac.new(b"bkwhsec_wrong_b", signed, hashlib.sha256).hexdigest()
    header = f"t={ts},v1={bad1},v1={bad2}"

    with pytest.raises(WebhookVerificationError, match="mismatch"):
        WebhookSignature.verify(
            payload=sample_event_body,
            signature_header=header,
            secret=webhook_secret,
        )


# ── typed events ───────────────────────────────────────────────────


def _verify(payload: dict[str, object], secret: str = "bkwhsec_typed") -> Event:
    body = json.dumps(payload).encode()
    return WebhookSignature.verify(
        payload=body, signature_header=_sign(body, secret), secret=secret
    )


_SUB = {
    "id": "sub_1",
    "object": "subscription",
    "status": "active",
    "renewal_state": "paused",
    "customer_id": "cus_1",
}


def test_known_type_verifies_to_its_typed_class() -> None:
    event = _verify(
        {
            "id": "evt_1",
            "object": "event",
            "type": "subscription.updated",
            "created": 1,
            "livemode": False,
            "data": _SUB,
            "previous_attributes": {"renewal_state": "auto_renew"},
        }
    )
    assert isinstance(event, SubscriptionUpdatedEvent)
    assert isinstance(event.data, Subscription)
    assert event.data.renewal_state == "paused"
    assert event.previous_attributes == {"renewal_state": "auto_renew"}
    # The 0.x dict reads still work.
    assert event["type"] == "subscription.updated"
    assert event["data"]["customer_id"] == "cus_1"
    assert event.to_dict()["data"] == _SUB


def test_null_previous_attributes_is_none_not_a_crash() -> None:
    """The 0.x README example did ``event["previous_attributes"]["status"]``
    behind an ``in`` check, which a key that is always present and usually
    null turns into ``TypeError``. Test for ``None`` instead."""
    event = _verify(
        {
            "id": "evt_2",
            "object": "event",
            "type": "customer.created",
            "created": 1,
            "livemode": False,
            "data": {"id": "cus_1", "object": "customer"},
            "previous_attributes": None,
        }
    )
    assert "previous_attributes" in event
    assert event.previous_attributes is None
    previous: dict[str, object] = event.previous_attributes or {}
    assert previous.get("status") is None


def test_unknown_type_verifies_to_a_plain_event() -> None:
    event = _verify(
        {
            "id": "evt_3",
            "object": "event",
            "type": "ledger.rebalanced",
            "created": 1,
            "livemode": True,
            "data": {"id": "led_1", "amount_cents": 5},
            "previous_attributes": None,
            "added_in_2027": [1],
        }
    )
    assert type(event) is Event
    assert event.data["amount_cents"] == 5
    assert event["added_in_2027"] == [1]


@pytest.mark.parametrize("body", [b"[1, 2]", b'"evt"', b"7", b"null"])
def test_a_signed_body_that_is_not_an_object_is_refused(body: bytes) -> None:
    secret = "bkwhsec_shape"
    with pytest.raises(WebhookVerificationError, match="not an event object"):
        WebhookSignature.verify(payload=body, signature_header=_sign(body, secret), secret=secret)


def test_verification_error_is_a_billkit_error() -> None:
    with pytest.raises(BillKitError):
        WebhookSignature.verify(payload=b"{}", signature_header=None, secret="s")


# ── the timestamp is ASCII digits of bounded length ───────────────────


@pytest.mark.parametrize(
    "ts",
    [
        "9" * 400,  # parses as int, then overflowed float in the tolerance check
        "1" * 13,
        "+1700000000",
        "1_700_000_000",
        " 1700000000x",
        "\u0661\u0667\u0660\u0660\u0660\u0660\u0660\u0660\u0660\u0660",  # Arabic-Indic digits
        "",
    ],
)
def test_malformed_timestamp_is_a_verification_error(
    ts: str, sample_event_body: bytes, webhook_secret: str
) -> None:
    v1 = hmac.new(
        webhook_secret.encode(), f"{ts}.".encode() + sample_event_body, hashlib.sha256
    ).hexdigest()
    # "Malformed", not "timestamp": a stale one says "timestamp" too, and
    # every value here would also fail the tolerance check if parsed.
    with pytest.raises(WebhookVerificationError, match="Malformed"):
        WebhookSignature.verify(
            payload=sample_event_body,
            signature_header=f"t={ts},v1={v1}",
            secret=webhook_secret,
        )


def test_twelve_digit_timestamp_is_parsed_then_judged_stale(
    sample_event_body: bytes, webhook_secret: str
) -> None:
    header = _sign(sample_event_body, webhook_secret, ts=999_999_999_999)
    with pytest.raises(WebhookVerificationError, match="tolerance"):
        WebhookSignature.verify(
            payload=sample_event_body, signature_header=header, secret=webhook_secret
        )


# ── an empty secret is a configuration mistake ────────────────────────


@pytest.mark.parametrize("tolerance", [float("nan"), float("inf"), -1, -0.5])
def test_unusable_tolerance_raises_value_error(
    tolerance: float, sample_event_body: bytes, webhook_secret: str
) -> None:
    # A signature an hour old: NaN compares false against everything, so
    # without the guard it would be accepted as fresh.
    header = _sign(sample_event_body, webhook_secret, ts=int(time.time()) - 3600)
    with pytest.raises(ValueError, match="tolerance_seconds"):
        WebhookSignature.verify(
            payload=sample_event_body,
            signature_header=header,
            secret=webhook_secret,
            tolerance_seconds=tolerance,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("secret", ["", "   ", "\t\n"])
def test_blank_secret_raises_value_error_before_verifying(
    secret: str, sample_event_body: bytes
) -> None:
    # Signed with the very same blank key, so only the guard can refuse it.
    header = _sign(sample_event_body, secret)
    with pytest.raises(ValueError, match="secret"):
        WebhookSignature.verify(payload=sample_event_body, signature_header=header, secret=secret)
