"""Auto-pagination + new endpoint surfaces (deliveries, tenant
credential rotation).

The pagination tests stub out two pages and verify the iterator
walks both. The endpoint tests verify the path + idempotency header
shape; full happy-path coverage is the API's responsibility."""

from __future__ import annotations

import httpx
import pytest
import respx

from billkit import AsyncBillKit, BillKit


@respx.mock
def test_customers_iter_walks_two_pages(sync_client: BillKit) -> None:
    """``iter()`` follows cursor-based pagination across pages."""
    route = respx.get("https://test.billkit.eu/v1/customers").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"id": "cus_1", "object": "customer"},
                        {"id": "cus_2", "object": "customer"},
                    ],
                    "has_more": True,
                },
            ),
            httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"id": "cus_3", "object": "customer"}],
                    "has_more": False,
                },
            ),
        ]
    )
    ids = [c["id"] for c in sync_client.customers.iter()]
    assert ids == ["cus_1", "cus_2", "cus_3"]
    assert route.call_count == 2
    # Second call uses the last id of page one as the cursor.
    assert route.calls[1].request.url.params.get("starting_after") == "cus_2"


@respx.mock
def test_iter_stops_when_has_more_false(sync_client: BillKit) -> None:
    """Single page, ``has_more=False`` → one HTTP call, exact rows yielded."""
    route = respx.get("https://test.billkit.eu/v1/subscriptions").mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"id": "sub_1", "object": "subscription"}],
                "has_more": False,
            },
        )
    )
    rows = list(sync_client.subscriptions.iter())
    assert len(rows) == 1
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_async_iter_walks_two_pages(async_client: AsyncBillKit) -> None:
    """Same as the sync iterator test, but async."""
    respx.get("https://test.billkit.eu/v1/events").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"id": "evt_1", "type": "customer.created"},
                        {"id": "evt_2", "type": "customer.created"},
                    ],
                    "has_more": True,
                },
            ),
            httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"id": "evt_3", "type": "customer.created"}],
                    "has_more": False,
                },
            ),
        ]
    )
    ids = [e["id"] async for e in async_client.events.iter()]
    assert ids == ["evt_1", "evt_2", "evt_3"]


@pytest.mark.asyncio
@respx.mock
async def test_list_deliveries_async(async_client: AsyncBillKit) -> None:
    """The new deliveries route is reachable from the SDK."""
    route = respx.get("https://test.billkit.eu/v1/webhook_endpoints/we_1/deliveries").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [], "has_more": False})
    )
    page = await async_client.webhook_endpoints.list_deliveries("we_1")
    assert page["object"] == "list"
    assert route.called


@respx.mock
def test_redeliver_delivery_posts(sync_client: BillKit) -> None:
    """Redeliver hits the right path with the idempotency header."""
    route = respx.post(
        "https://test.billkit.eu/v1/webhook_endpoints/we_1/deliveries/abc/redeliver"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "abc",
                "object": "webhook_delivery",
                "status": "pending",
                "attempt_count": 4,
            },
        )
    )
    row = sync_client.webhook_endpoints.redeliver("we_1", "abc")
    assert row["status"] == "pending"
    assert route.called
    assert "Idempotency-Key" in route.calls.last.request.headers


@respx.mock
def test_rotate_provider_credential_posts(sync_client: BillKit) -> None:
    """Tenant credential rotation hits the right path with the body shape."""
    route = respx.post("https://test.billkit.eu/v1/tenant/provider_credential").mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "tenant_provider_credential",
                "tenant_id": "00000000-0000-0000-0000-000000000000",
                "provider": "mollie",
                "mode": "test",
                "rotated_at": 1700000000,
            },
        )
    )
    resp = sync_client.tenant.rotate_provider_credential(api_key="test_freshkey")
    assert resp["mode"] == "test"
    assert route.called
    body = route.calls.last.request.read().decode()
    assert "test_freshkey" in body
    assert "mollie" in body


@respx.mock
def test_tenant_set_portal_branding(sync_client: BillKit) -> None:
    """Branding update sends only the supplied fields."""
    route = respx.post("https://test.billkit.eu/v1/tenant/portal_branding").mock(
        return_value=httpx.Response(
            200, json={"object": "tenant_portal_branding", "business_name": "Acme"}
        )
    )
    resp = sync_client.tenant.set_portal_branding(business_name="Acme")
    assert resp["business_name"] == "Acme"
    body = route.calls.last.request.read().decode()
    assert "Acme" in body
    # Unset fields are not sent.
    assert "logo_url" not in body
