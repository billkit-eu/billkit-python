"""Transport and surface hardening from the 0.10.0 audit.

- A blank caller ``idempotency_key`` is treated as unset, so the POST the
  retry loop re-sends always carries one stable, non-empty key.
- A JSON call never follows a redirect (the PDF path still does; see
  ``test_documents.py``).
- An empty id is refused locally instead of building ``/v1/customers/``.
- Test clocks are reachable, and ``test_clock_id`` rides on customer create.
- The event-type constants, and that an unknown type never raises.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from billkit import (
    EVENT_TYPES,
    AsyncBillKit,
    BillKit,
    Event,
    EventType,
    InvalidRequestError,
    WebhookSignature,
    is_known_event_type,
)
from tests.conftest import last_request_body

_BAD_GATEWAY = httpx.Response(502, json={"error": {"type": "api_error", "message": "upstream"}})


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
@respx.mock
def test_blank_idempotency_key_is_generated_once_and_reused(
    sync_client: BillKit, blank: str
) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        side_effect=[_BAD_GATEWAY, _BAD_GATEWAY, httpx.Response(200, json={"id": "cus_1"})]
    )
    sync_client.customers.create(email="a@b.co", idempotency_key=blank)

    keys = [call.request.headers.get("Idempotency-Key") for call in route.calls]
    assert len(keys) == 3
    assert keys[0] is not None
    assert keys[0].startswith("sdk-")
    assert len(keys[0]) == 40
    assert keys == [keys[0]] * 3


@pytest.mark.parametrize("blank", ["", "  "])
@respx.mock
async def test_blank_idempotency_key_is_generated_async(
    async_client: AsyncBillKit, blank: str
) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        side_effect=[_BAD_GATEWAY, _BAD_GATEWAY, httpx.Response(200, json={"id": "cus_1"})]
    )
    await async_client.customers.create(email="a@b.co", idempotency_key=blank)

    keys = [call.request.headers.get("Idempotency-Key") for call in route.calls]
    assert keys[0] is not None
    assert keys[0].startswith("sdk-")
    assert keys == [keys[0]] * 3


@respx.mock
def test_a_real_caller_key_is_sent_verbatim(sync_client: BillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        side_effect=[_BAD_GATEWAY, httpx.Response(200, json={})]
    )
    sync_client.customers.create(idempotency_key="order-42")
    assert [c.request.headers["Idempotency-Key"] for c in route.calls] == ["order-42"] * 2


@respx.mock
def test_a_json_call_does_not_follow_a_redirect(sync_client: BillKit) -> None:
    """Already true in python; pinned so it stays the shared contract."""
    respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(307, headers={"Location": "https://elsewhere.example/"})
    )
    target = respx.post("https://elsewhere.example/").mock(return_value=httpx.Response(200))
    with pytest.raises(InvalidRequestError):
        sync_client.customers.create(email="ada@example.com")
    assert target.call_count == 0


_EMPTY_ID_CALLS: list[tuple[str, Callable[[BillKit], Any]]] = [
    ("customers.retrieve", lambda c: c.customers.retrieve("")),
    ("customers.update", lambda c: c.customers.update("  ", name="x")),
    ("customers.delete", lambda c: c.customers.delete("")),
    ("subscriptions.cancel", lambda c: c.subscriptions.cancel("")),
    ("invoices.retrieve_pdf", lambda c: c.invoices.retrieve_pdf("")),
    ("test_clocks.advance", lambda c: c.test_clocks.advance("", frozen_time=1)),
    (
        "webhook_endpoints.retrieve_delivery",
        lambda c: c.webhook_endpoints.retrieve_delivery("we_1", ""),
    ),
]


@pytest.mark.parametrize(("name", "call"), _EMPTY_ID_CALLS, ids=[n for n, _ in _EMPTY_ID_CALLS])
def test_empty_id_is_refused_before_a_request(
    sync_client: BillKit, name: str, call: Callable[[BillKit], Any]
) -> None:
    with respx.mock(assert_all_called=False) as router:
        catch_all = router.route().mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="an id is required"):
            call(sync_client)
    assert catch_all.call_count == 0


@pytest.mark.parametrize("dot", [".", ".."])
def test_dot_segment_id_is_refused_before_a_request(sync_client: BillKit, dot: str) -> None:
    # Percent-encoding leaves ``.`` and ``..`` alone and httpx resolves them:
    # ``retrieve("..")`` must not become ``GET /v1/``.
    with respx.mock(assert_all_called=False) as router:
        catch_all = router.route().mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="is not an id"):
            sync_client.customers.retrieve(dot)
        with pytest.raises(ValueError, match="is not an id"):
            sync_client.webhook_endpoints.retrieve_delivery("we_1", dot)
    assert catch_all.call_count == 0


async def test_empty_id_is_refused_async(async_client: AsyncBillKit) -> None:
    with respx.mock(assert_all_called=False) as router:
        catch_all = router.route().mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="an id is required"):
            await async_client.customers.retrieve("")
    assert catch_all.call_count == 0


@respx.mock
def test_test_clocks_routes(sync_client: BillKit) -> None:
    create = respx.post("https://test.billkit.eu/v1/test_clocks").mock(
        return_value=httpx.Response(200, json={"id": "clock_1"})
    )
    get = respx.get("https://test.billkit.eu/v1/test_clocks/clock_1").mock(
        return_value=httpx.Response(200, json={"id": "clock_1"})
    )
    advance = respx.post("https://test.billkit.eu/v1/test_clocks/clock_1/advance").mock(
        return_value=httpx.Response(200, json={"id": "clock_1"})
    )
    delete = respx.delete("https://test.billkit.eu/v1/test_clocks/clock_1").mock(
        return_value=httpx.Response(
            200, json={"id": "clock_1", "object": "test_clock", "deleted": True}
        )
    )
    listing = respx.get("https://test.billkit.eu/v1/test_clocks").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [], "has_more": False})
    )

    sync_client.test_clocks.create(frozen_time=1_800_000_000, name="Renewals")
    sync_client.test_clocks.retrieve("clock_1")
    sync_client.test_clocks.advance("clock_1", frozen_time=1_900_000_000, idempotency_key="adv-1")
    assert sync_client.test_clocks.delete("clock_1")["deleted"] is True
    sync_client.test_clocks.list(limit=5)

    assert last_request_body(create) == {"frozen_time": 1_800_000_000, "name": "Renewals"}
    assert create.calls[0].request.headers["Idempotency-Key"].startswith("sdk-")
    assert get.call_count == 1
    assert last_request_body(advance) == {"frozen_time": 1_900_000_000}
    assert advance.calls[0].request.headers["Idempotency-Key"] == "adv-1"
    assert delete.calls[0].request.headers["Idempotency-Key"].startswith("sdk-")
    assert listing.calls[0].request.url.params["limit"] == "5"


@respx.mock
async def test_test_clocks_async_iter_walks_pages(async_client: AsyncBillKit) -> None:
    respx.get("https://test.billkit.eu/v1/test_clocks").mock(
        side_effect=[
            httpx.Response(
                200, json={"object": "list", "data": [{"id": "clock_2"}], "has_more": True}
            ),
            httpx.Response(
                200, json={"object": "list", "data": [{"id": "clock_1"}], "has_more": False}
            ),
        ]
    )
    ids = [clock["id"] async for clock in async_client.test_clocks.iter()]
    assert ids == ["clock_2", "clock_1"]


@respx.mock
async def test_customer_create_carries_test_clock(
    sync_client: BillKit, async_client: AsyncBillKit
) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(200, json={})
    )
    sync_client.customers.create(email="a@b.co", test_clock_id="clock_1")
    assert last_request_body(route) == {"email": "a@b.co", "test_clock_id": "clock_1"}
    await async_client.customers.create(email="a@b.co", test_clock_id="clock_1")
    assert last_request_body(route) == {"email": "a@b.co", "test_clock_id": "clock_1"}


def test_event_type_constants() -> None:
    assert str(EventType.INVOICE_PAID) == "invoice.paid"
    assert "invoice.paid" in {EventType.INVOICE_PAID}
    assert "subscription.updated" in EVENT_TYPES
    assert len(EVENT_TYPES) == len(EventType)
    assert is_known_event_type("invoice.paid")
    assert not is_known_event_type("invoice.something_new")


def test_unknown_event_type_verifies_and_keeps_previous_attributes() -> None:
    secret = "bkwhsec_unit"
    body = json.dumps(
        {
            "id": "evt_1",
            "object": "event",
            "type": "subscription.something_added_later",
            "created": 1,
            "livemode": False,
            "data": {"id": "sub_1", "status": "active"},
            "previous_attributes": {"status": "trialing"},
        }
    ).encode()
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()

    event = WebhookSignature.verify(
        payload=body, signature_header=f"t={ts},v1={sig}", secret=secret
    )
    assert type(event) is Event
    assert not is_known_event_type(event["type"])
    assert event["previous_attributes"] == {"status": "trialing"}
    assert event.previous_attributes == {"status": "trialing"}
    assert event["data"].to_dict() == {"id": "sub_1", "status": "active"}
    assert event.data["status"] == "active"
    # An unknown type's ``data`` is a generic object: unknown fields read
    # as attributes too, at runtime.
    assert getattr(event.data, "status") == "active"  # noqa: B009
