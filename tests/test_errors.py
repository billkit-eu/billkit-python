"""Verify API error envelopes map to the right exception subclasses."""

from __future__ import annotations

import httpx
import pytest
import respx

from billkit import (
    AsyncBillKit,
    AuthenticationError,
    BillKitError,
    ConflictError,
    InvalidRequestError,
    PermissionError,
    RateLimitError,
    ResourceMissingError,
    ServerError,
)


@pytest.mark.asyncio
@respx.mock
async def test_401_maps_to_authentication_error(async_client: AsyncBillKit) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(
            401,
            json={
                "error": {
                    "type": "authentication_error",
                    "code": "missing_authorization",
                    "message": "No API key provided.",
                }
            },
        )
    )
    with pytest.raises(AuthenticationError) as exc_info:
        await async_client.customers.retrieve("cus_1")
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "missing_authorization"


@pytest.mark.asyncio
@respx.mock
async def test_403_maps_to_permission_error(async_client: AsyncBillKit) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(
            403,
            json={"error": {"type": "permission_error", "message": "Insufficient scope."}},
        )
    )
    with pytest.raises(PermissionError):
        await async_client.customers.retrieve("cus_1")


@pytest.mark.asyncio
@respx.mock
async def test_404_maps_to_resource_missing(async_client: AsyncBillKit) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_x").mock(
        return_value=httpx.Response(
            404,
            json={"error": {"type": "invalid_request_error", "message": "No such customer."}},
        )
    )
    with pytest.raises(ResourceMissingError):
        await async_client.customers.retrieve("cus_x")


@pytest.mark.asyncio
@respx.mock
async def test_409_maps_to_conflict(async_client: AsyncBillKit) -> None:
    respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": {
                    "type": "idempotency_error",
                    "code": "idempotency_key_in_use",
                    "message": "Different body for same key.",
                }
            },
        )
    )
    with pytest.raises(ConflictError) as exc_info:
        await async_client.customers.create(email="a@b.co")
    assert exc_info.value.code == "idempotency_key_in_use"


@pytest.mark.asyncio
@respx.mock
async def test_422_invalid_param_records_param_field(
    async_client: AsyncBillKit,
) -> None:
    respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(
            422,
            json={
                "error": {
                    "type": "invalid_request_error",
                    "code": "parameter_invalid",
                    "param": "email",
                    "message": "Email must be valid.",
                }
            },
        )
    )
    with pytest.raises(InvalidRequestError) as exc_info:
        await async_client.customers.create(email="not-an-email")
    assert exc_info.value.param == "email"


@pytest.mark.asyncio
@respx.mock
async def test_429_maps_to_rate_limit_with_retry_after(
    async_client: AsyncBillKit,
) -> None:
    # 429 isn't auto-retried (it could mean a tenant-specific quota), so
    # the exception surfaces and the caller decides whether to honor
    # ``retry_after``.
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(
            429,
            json={"error": {"type": "rate_limit_error", "message": "Too fast."}},
            headers={"Retry-After": "2"},
        )
    )
    with pytest.raises(RateLimitError) as exc_info:
        await async_client.customers.retrieve("cus_1")
    assert exc_info.value.retry_after == 2.0


@pytest.mark.asyncio
@respx.mock
async def test_5xx_with_no_envelope_maps_to_server_error(
    async_client: AsyncBillKit,
) -> None:
    # Traefik / nginx 502: no envelope, just an HTML body. SDK must
    # still raise a typed error rather than crash on JSON decode.
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(502, text="<html>bad gateway</html>")
    )
    with pytest.raises(ServerError) as exc_info:
        await async_client.customers.retrieve("cus_1")
    assert exc_info.value.status_code == 502
    assert isinstance(exc_info.value, BillKitError)
    assert exc_info.value.raw_body is None
