"""The SDK's logging must be opt-in and leak-free.

Two properties are load-bearing and easy to regress:

1. **Silent by default.** A library that writes to the root logger, or
   calls ``basicConfig``, takes over its host application's logging.
   The SDK must emit nothing until the application raises the level on
   the ``"billkit"`` logger itself.
2. **No secrets, ever.** API keys, request/response bodies and query
   strings must never reach a log record. The caller's log sink is not
   somewhere a payments SDK gets to put customer PII.
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from billkit import AsyncBillKit, BillKit, ServerError, logger


def test_logger_is_the_named_billkit_logger() -> None:
    assert logger is logging.getLogger("billkit")
    assert logger.name == "billkit"


def test_logger_has_a_null_handler() -> None:
    """The library-author contract: somewhere for records to go so
    nothing is printed and no "No handlers could be found" warning
    fires, and no *real* handler that would steal output."""
    handlers = logging.getLogger("billkit").handlers
    assert any(isinstance(h, logging.NullHandler) for h in handlers)
    assert all(isinstance(h, logging.NullHandler) for h in handlers), (
        "The SDK must not attach a real handler; that is the application's job."
    )


def test_library_does_not_configure_the_root_logger() -> None:
    """Importing the SDK must not touch anything it doesn't own."""
    root = logging.getLogger()
    assert root.level == logging.WARNING, (
        "Importing billkit changed the root logger level; that is hijacking."
    )
    assert logger.level == logging.NOTSET, (
        "The SDK must not set its own level either; the application decides."
    )
    assert logger.propagate is True, (
        "propagate=False would hide SDK records from the application's handlers."
    )


@respx.mock
def test_silent_by_default(sync_client: BillKit, caplog: pytest.LogCaptureFixture) -> None:
    """With the logger left alone, a successful call logs nothing at
    WARNING or above, the level an unconfigured app would surface."""
    respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(200, json={"id": "cus_1"})
    )
    with caplog.at_level(logging.WARNING, logger="billkit"):
        sync_client.customers.create(email="a@b.co")
    assert caplog.records == []


@respx.mock
def test_debug_logs_request_and_response_when_enabled(
    sync_client: BillKit, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(
            200, json={"id": "cus_1"}, headers={"x-request-id": "req_abc"}
        )
    )
    with caplog.at_level(logging.DEBUG, logger="billkit"):
        sync_client.customers.create(email="a@b.co")

    messages = [r.getMessage() for r in caplog.records]
    assert any("BillKit request POST" in m and "/v1/customers" in m for m in messages)
    assert any("BillKit response POST" in m and "-> 200" in m for m in messages)
    # The request id is the whole point of the response line; it's what
    # a tenant quotes to support.
    assert any("request_id=req_abc" in m for m in messages)


@respx.mock
def test_retry_logs_a_warning_with_the_attempt(
    sync_client: BillKit, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post("https://test.billkit.eu/v1/customers").mock(
        side_effect=[
            httpx.Response(503, json={"error": {"type": "api_error", "message": "down"}}),
            httpx.Response(200, json={"id": "cus_1"}),
        ]
    )
    with caplog.at_level(logging.DEBUG, logger="billkit"):
        sync_client.customers.create(email="a@b.co")

    retries = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(retries) == 1
    assert "BillKit retrying POST" in retries[0].getMessage()
    assert "HTTP 503" in retries[0].getMessage()


@respx.mock
def test_final_failure_is_raised_not_logged(
    sync_client: BillKit, caplog: pytest.LogCaptureFixture
) -> None:
    """The exception carries everything the caller needs. Logging it here
    too would duplicate an entry they never asked for and can't suppress."""
    respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(500, json={"error": {"type": "api_error", "message": "boom"}})
    )
    with caplog.at_level(logging.DEBUG, logger="billkit"), pytest.raises(ServerError):
        sync_client.customers.create(email="a@b.co")

    assert not any(r.levelno >= logging.ERROR for r in caplog.records), (
        "The SDK logged the failure it also raised; the caller now has it twice."
    )


@respx.mock
def test_logs_never_contain_the_api_key_body_or_query(
    sync_client: BillKit, caplog: pytest.LogCaptureFixture
) -> None:
    """Three leak paths, one test: the auth header, the request body
    (PII in), the response body (PII out), and the query string."""
    respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(
            200, json={"id": "cus_1", "email": "ada@example.com", "name": "Ada Lovelace"}
        )
    )
    respx.get("https://test.billkit.eu/v1/audit_logs").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [], "has_more": False})
    )

    with caplog.at_level(logging.DEBUG, logger="billkit"):
        sync_client.customers.create(email="ada@example.com", name="Ada Lovelace")
        sync_client.audit_logs.list(actor_id="act_secret_filter")

    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert blob, "Expected DEBUG records; the rest of this test would pass vacuously."
    assert "sk_test_unit" not in blob, "The API key reached a log record."
    assert "Bearer" not in blob, "The Authorization header reached a log record."
    assert "ada@example.com" not in blob, "A request/response body (PII) reached a log record."
    assert "Ada Lovelace" not in blob, "A request/response body (PII) reached a log record."
    assert "act_secret_filter" not in blob, "A query-string value reached a log record."
    assert "?" not in blob, "A query string was appended to the logged URL."


@pytest.mark.asyncio
@respx.mock
async def test_async_transport_logs_the_same_lines(
    async_client: AsyncBillKit, caplog: pytest.LogCaptureFixture
) -> None:
    """Sync and async share the helpers, so the surfaces must not drift."""
    respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(200, json={"id": "cus_1"})
    )
    with caplog.at_level(logging.DEBUG, logger="billkit"):
        await async_client.customers.create(email="a@b.co")

    messages = [r.getMessage() for r in caplog.records]
    assert any("BillKit request POST" in m for m in messages)
    assert any("BillKit response POST" in m and "-> 200" in m for m in messages)
