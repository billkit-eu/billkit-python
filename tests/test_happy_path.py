"""Happy-path coverage: each resource issues the expected request +
returns the decoded JSON."""

from __future__ import annotations

import httpx
import pytest
import respx

from billkit import AsyncBillKit, BillKit
from tests.conftest import assert_idempotency_header


@pytest.mark.asyncio
@respx.mock
async def test_create_customer_async(async_client: AsyncBillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(
            200, json={"id": "cus_1", "object": "customer", "email": "a@b.co"}
        )
    )
    customer = await async_client.customers.create(email="a@b.co", name="Ada")

    assert customer["id"] == "cus_1"
    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer sk_test_unit"
    assert_idempotency_header(request)


@respx.mock
def test_create_customer_sync(sync_client: BillKit) -> None:
    respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(200, json={"id": "cus_1", "object": "customer"})
    )
    customer = sync_client.customers.create(email="a@b.co")
    assert customer["id"] == "cus_1"


@pytest.mark.asyncio
@respx.mock
async def test_products_resource_create_and_update(async_client: AsyncBillKit) -> None:
    create_route = respx.post("https://test.billkit.eu/v1/products").mock(
        return_value=httpx.Response(200, json={"id": "prod_1", "name": "Pro"})
    )
    update_route = respx.post("https://test.billkit.eu/v1/products/prod_1").mock(
        return_value=httpx.Response(200, json={"id": "prod_1", "active": False})
    )

    product = await async_client.products.create(
        name="Pro",
        description="Hosted billing for SaaS",
        marketing_features=["Checkout", "Subscriptions"],
    )
    archived = await async_client.products.update("prod_1", active=False)

    assert product["id"] == "prod_1"
    assert archived["active"] is False
    assert b"marketing_features" in create_route.calls.last.request.read()
    assert b"active" in update_route.calls.last.request.read()


@pytest.mark.asyncio
@respx.mock
async def test_price_create_uses_product_id_and_trial_fields(
    async_client: AsyncBillKit,
) -> None:
    route = respx.post("https://test.billkit.eu/v1/prices").mock(
        return_value=httpx.Response(200, json={"id": "price_1", "product_id": "prod_1"})
    )

    price = await async_client.prices.create(
        product_id="prod_1",
        amount_cents=999,
        currency="EUR",
        interval="month",
        trial_days=14,
        payment_methods=["creditcard", "directdebit"],
    )

    body = route.calls.last.request.read()
    assert price["id"] == "price_1"
    assert b"prod_1" in body
    assert b"trial_days" in body
    assert b"payment_methods" in body
    assert b"product_name" not in body


@pytest.mark.asyncio
@respx.mock
async def test_retrieve_subscription_no_idempotency_on_get(
    async_client: AsyncBillKit,
) -> None:
    route = respx.get("https://test.billkit.eu/v1/subscriptions/sub_1").mock(
        return_value=httpx.Response(200, json={"id": "sub_1"})
    )
    await async_client.subscriptions.retrieve("sub_1")
    assert route.called
    # GETs must NOT carry an Idempotency-Key. Server-side they're treated
    # as read-only and the header would be ignored, but generating
    # garbage headers per-request is wasteful.
    assert "Idempotency-Key" not in route.calls.last.request.headers


@pytest.mark.asyncio
@respx.mock
async def test_list_passes_pagination_params(async_client: AsyncBillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [], "has_more": False})
    )
    await async_client.customers.list(limit=25, starting_after="cus_x")
    assert route.called
    params = route.calls.last.request.url.params
    assert params["limit"] == "25"
    assert params["starting_after"] == "cus_x"


@pytest.mark.asyncio
@respx.mock
async def test_subscription_cancel_uses_post(async_client: AsyncBillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/subscriptions/sub_1/cancel").mock(
        return_value=httpx.Response(200, json={"id": "sub_1", "status": "canceled"})
    )
    sub = await async_client.subscriptions.cancel("sub_1")
    assert sub["status"] == "canceled"
    assert route.called
    assert_idempotency_header(route.calls.last.request)


@pytest.mark.asyncio
@respx.mock
async def test_create_refund_supports_subscription_id(
    async_client: AsyncBillKit,
) -> None:
    route = respx.post("https://test.billkit.eu/v1/refunds").mock(
        return_value=httpx.Response(200, json={"id": "re_1"})
    )
    await async_client.refunds.create(subscription_id="sub_1", reason="user_requested")
    body = route.calls.last.request.read()
    assert b"sub_1" in body
    assert b"user_requested" in body
    assert b"payment_id" not in body  # _drop_none stripped it


@pytest.mark.asyncio
@respx.mock
async def test_custom_idempotency_key_passes_through(
    async_client: AsyncBillKit,
) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(200, json={"id": "cus_1"})
    )
    await async_client.customers.create(email="a@b.co", idempotency_key="my-key")
    assert route.calls.last.request.headers["Idempotency-Key"] == "my-key"
