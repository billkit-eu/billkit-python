"""Verify retry policy: 5xx + network failures retry, 4xx do not,
the SDK-generated Idempotency-Key stays stable across attempts so
the server can replay."""

from __future__ import annotations

import httpx
import pytest
import respx

from billkit import AsyncBillKit, AuthenticationError, RateLimitError, ServerError


@pytest.mark.asyncio
@respx.mock
async def test_5xx_is_retried_then_succeeds(async_client: AsyncBillKit) -> None:
    # FAST_RETRY in conftest is max_attempts=3, no backoff.
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        side_effect=[
            httpx.Response(503, json={"error": {"type": "api_error", "message": "down"}}),
            httpx.Response(503, json={"error": {"type": "api_error", "message": "still"}}),
            httpx.Response(200, json={"id": "cus_1"}),
        ]
    )
    customer = await async_client.customers.create(email="a@b.co")
    assert customer["id"] == "cus_1"
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_5xx_exhausts_budget_then_raises(async_client: AsyncBillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(
            500, json={"error": {"type": "api_error", "message": "boom"}}
        )
    )
    with pytest.raises(ServerError):
        await async_client.customers.create(email="a@b.co")
    assert route.call_count == 3  # max_attempts in FAST_RETRY


@pytest.mark.asyncio
@respx.mock
async def test_4xx_is_not_retried(async_client: AsyncBillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(
            401, json={"error": {"type": "authentication_error", "message": "bad"}}
        )
    )
    with pytest.raises(AuthenticationError):
        await async_client.customers.retrieve("cus_1")
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_429_with_short_retry_after_is_retried(
    async_client: AsyncBillKit,
) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        side_effect=[
            httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={
                    "error": {
                        "type": "rate_limit_error",
                        "message": "slow down",
                    }
                },
            ),
            httpx.Response(200, json={"id": "cus_1"}),
        ]
    )

    customer = await async_client.customers.create(email="a@b.co")

    assert customer["id"] == "cus_1"
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_429_without_retry_after_is_not_retried(
    async_client: AsyncBillKit,
) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(
            429,
            json={"error": {"type": "rate_limit_error", "message": "slow down"}},
        )
    )

    with pytest.raises(RateLimitError):
        await async_client.customers.create(email="a@b.co")

    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_idempotency_key_stable_across_retries(
    async_client: AsyncBillKit,
) -> None:
    """The server-side replay protection only works if the *same* key
    arrives on every retry. The SDK must NOT regenerate per attempt."""
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        side_effect=[
            httpx.Response(503, json={"error": {"type": "api_error", "message": "x"}}),
            httpx.Response(200, json={"id": "cus_1"}),
        ]
    )
    await async_client.customers.create(email="a@b.co")
    first_key = route.calls[0].request.headers["Idempotency-Key"]
    second_key = route.calls[1].request.headers["Idempotency-Key"]
    assert first_key == second_key
    assert first_key.startswith("sdk-")
