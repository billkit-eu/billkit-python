"""Verify retry policy: 5xx (but not 501) and network failures retry, 4xx
do not (except ``409 idempotency_in_progress``), and the SDK-generated
Idempotency-Key stays stable across attempts so the server can replay."""

from __future__ import annotations

import httpx
import pytest
import respx

from billkit import (
    AsyncBillKit,
    AuthenticationError,
    BillKit,
    ConflictError,
    RateLimitError,
    ServerError,
)
from billkit._retry import RetryPolicy


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
        return_value=httpx.Response(500, json={"error": {"type": "api_error", "message": "boom"}})
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


def _conflict(code: str) -> httpx.Response:
    return httpx.Response(
        409, json={"error": {"type": "conflict", "code": code, "message": "in flight"}}
    )


@pytest.mark.asyncio
@respx.mock
async def test_409_in_progress_is_retried_then_replays(async_client: AsyncBillKit) -> None:
    """The one 4xx worth retrying.

    ``idempotency_in_progress`` says a request carrying this same key is
    still running elsewhere, so the charge may already have happened.
    Surfacing it invites the caller to retry with a *fresh* key, which is
    what turns one charge into two.
    """
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        side_effect=[
            _conflict("idempotency_in_progress"),
            httpx.Response(200, json={"id": "cus_1"}),
        ]
    )
    customer = await async_client.customers.create(email="a@b.co")
    assert customer["id"] == "cus_1"
    assert route.call_count == 2
    # The whole reason retrying is safe: the second attempt replays rather
    # than re-charging, and only an unchanged key can do that.
    keys = {call.request.headers["idempotency-key"] for call in route.calls}
    assert len(keys) == 1


@pytest.mark.asyncio
@respx.mock
async def test_409_with_any_other_code_is_not_retried(async_client: AsyncBillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=_conflict("idempotency_key_in_use")
    )
    with pytest.raises(ConflictError):
        await async_client.customers.create(email="a@b.co")
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_409_without_a_code_is_not_retried(async_client: AsyncBillKit) -> None:
    """A conflict the envelope does not name is a caller-fault conflict."""
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(409, json={"error": {"type": "conflict", "message": "clash"}})
    )
    with pytest.raises(ConflictError):
        await async_client.customers.create(email="a@b.co")
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_409_in_progress_retries_are_bounded(async_client: AsyncBillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=_conflict("idempotency_in_progress")
    )
    with pytest.raises(ConflictError):
        await async_client.customers.create(email="a@b.co")
    assert route.call_count == 3  # max_attempts in FAST_RETRY


# ── 501 is a permanent answer ─────────────────────────────────────────
#
# Not Implemented means the server will never do this, so a retry only adds
# the backoff before the same error. The PDF routes answer it on a
# deployment with rendering disabled.


def _not_implemented() -> httpx.Response:
    return httpx.Response(
        501,
        json={"error": {"type": "api_error", "code": "rendering_pending", "message": "no"}},
    )


@pytest.mark.asyncio
@respx.mock
async def test_501_is_not_retried_async(async_client: AsyncBillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/invoices/in_1/pdf").mock(
        return_value=_not_implemented()
    )
    with pytest.raises(ServerError) as exc_info:
        await async_client.invoices.retrieve_pdf("in_1")
    assert exc_info.value.status_code == 501
    assert exc_info.value.code == "rendering_pending"
    assert route.call_count == 1


@respx.mock
def test_501_is_not_retried_sync(sync_client: BillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(return_value=_not_implemented())
    with pytest.raises(ServerError):
        sync_client.customers.create(email="a@b.co")
    assert route.call_count == 1


@respx.mock
def test_other_5xx_beside_501_still_retried_sync(sync_client: BillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        side_effect=[
            httpx.Response(502, text="bad gateway"),
            httpx.Response(200, json={"id": "cus_1"}),
        ]
    )
    assert sync_client.customers.create(email="a@b.co")["id"] == "cus_1"
    assert route.call_count == 2


# ── Construction guards ────────────────────────────────────────────


@pytest.mark.parametrize("attempts", [0, -1])
def test_retry_policy_refuses_fewer_than_one_attempt(attempts: int) -> None:
    """``max_attempts`` counts the first try; 0 would never send anything."""
    with pytest.raises(ValueError, match="max_attempts of at least 1"):
        RetryPolicy(max_attempts=attempts)


def test_retry_policy_of_one_attempt_is_no_retries() -> None:
    assert RetryPolicy(max_attempts=1).max_attempts == 1


@pytest.mark.parametrize("client_class", [BillKit, AsyncBillKit])
@pytest.mark.parametrize("key", ["", "   "])
def test_empty_api_key_is_refused_at_construction(
    client_class: type[BillKit] | type[AsyncBillKit], key: str
) -> None:
    with pytest.raises(ValueError, match="API key is empty"):
        client_class(api_key=key)


def test_empty_api_key_does_not_fall_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``""`` is a mistake, not "use the environment": falling
    back would hide the unset variable it usually comes from."""
    monkeypatch.setenv("BILLKIT_API_KEY", "bk_test_env")
    with pytest.raises(ValueError, match="API key is empty"):
        BillKit(api_key="")


@respx.mock
def test_none_api_key_still_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BILLKIT_API_KEY", "bk_test_env")
    route = respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(200, json={"id": "cus_1"})
    )
    with BillKit(base_url="https://test.billkit.eu") as client:
        client.customers.retrieve("cus_1")
    assert route.calls.last.request.headers["authorization"] == "Bearer bk_test_env"


# ── Async backoff is event-loop agnostic ───────────────────────────


@respx.mock
def test_async_retry_backoff_runs_under_trio() -> None:
    """The async backoff sleeps through anyio, so an ``AsyncBillKit`` under
    trio waits and retries. ``asyncio.sleep`` there raises on the first
    retry, before the second request is ever sent."""
    trio = pytest.importorskip("trio")
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        side_effect=[
            httpx.Response(503, json={"error": {"type": "api_error", "message": "down"}}),
            httpx.Response(200, json={"id": "cus_1"}),
        ]
    )
    policy = RetryPolicy(
        max_attempts=2,
        initial_backoff_seconds=0.01,
        backoff_multiplier=1.0,
        max_backoff_seconds=0.01,
        jitter=0.0,
    )

    async def main() -> str | None:
        async with AsyncBillKit(
            api_key="bk_test_unit", base_url="https://test.billkit.eu", retry_policy=policy
        ) as client:
            customer = await client.customers.create(email="a@b.co")
            return customer.id

    assert trio.run(main) == "cus_1"
    assert route.call_count == 2
