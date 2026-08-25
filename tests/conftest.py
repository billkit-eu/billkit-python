"""Shared fixtures.

Tests run against a mocked transport via :mod:`respx` so they
exercise the same code paths as a real HTTP call (URL building,
header construction, JSON encoding, retry, error mapping) without
the network.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import respx

from billkit import AsyncBillKit, BillKit
from billkit._retry import RetryPolicy

# Make retries fast so the test suite stays under a second.
FAST_RETRY = RetryPolicy(
    max_attempts=3,
    initial_backoff_seconds=0.0,
    backoff_multiplier=1.0,
    max_backoff_seconds=0.0,
    jitter=0.0,
)


@pytest.fixture
async def async_client() -> AsyncIterator[AsyncBillKit]:
    async with AsyncBillKit(
        api_key="sk_test_unit",
        base_url="https://test.billkit.eu",
        retry_policy=FAST_RETRY,
    ) as client:
        yield client


@pytest.fixture
def sync_client() -> Iterator[BillKit]:
    with BillKit(
        api_key="sk_test_unit",
        base_url="https://test.billkit.eu",
        retry_policy=FAST_RETRY,
    ) as client:
        yield client


@pytest.fixture
def webhook_secret() -> str:
    return "whsec_unit_test_secret"


@pytest.fixture
def sample_event_body() -> bytes:
    return b'{"id":"evt_1","type":"customer.created","data":{"id":"cus_1"}}'


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure a developer's BILLKIT_API_KEY env doesn't bleed into
    tests that assert the missing-key error message."""
    monkeypatch.delenv("BILLKIT_API_KEY", raising=False)


def assert_idempotency_header(request: httpx.Request) -> None:
    """Assert mutating requests carry an SDK-generated key."""
    key = request.headers.get("Idempotency-Key")
    assert key is not None, "Mutating request missing Idempotency-Key"
    assert key.startswith("sdk-"), f"Expected SDK-generated key, got {key!r}"


def last_request_body(route: respx.Route) -> dict[str, Any]:
    """Decode the last respx-captured request body as JSON.

    The raw-bytes → decode → ``json.loads`` chain appears in every
    body-shape regression test; centralising it keeps each test focused
    on the assertion that matters."""
    raw = route.calls.last.request.read().decode()
    return json.loads(raw)  # type: ignore[no-any-return]
