"""Verify API error responses map to the right exception subclasses.

The HTTP status is what picks the class; the envelope's ``type`` rides
along on the raised error but never decides.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from billkit import (
    APIError,
    AsyncBillKit,
    AuthenticationError,
    BillKit,
    BillKitError,
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    PaymentError,
    PermissionDeniedError,
    RateLimitError,
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


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("payment_refused", "The provider refused the refund: insufficient balance."),
        ("tenant_suspended", "This account is suspended."),
    ],
)
@respx.mock
def test_402_maps_to_payment_error(sync_client: BillKit, code: str, message: str) -> None:
    """A refused charge and a suspended account are both 402, and both are
    ``PaymentError``: not an ``InvalidRequestError``, which would tell the
    caller to change the request, and never retried."""
    route = respx.post("https://test.billkit.eu/v1/refunds").mock(
        return_value=httpx.Response(
            402,
            json={"error": {"type": "payment_error", "code": code, "message": message}},
        )
    )
    with pytest.raises(PaymentError) as exc_info:
        sync_client.refunds.create(payment_id="pay_1")
    err = exc_info.value
    assert not isinstance(err, InvalidRequestError)
    assert (err.status_code, err.type, err.code, err.message) == (
        402,
        "payment_error",
        code,
        message,
    )
    assert route.call_count == 1


@respx.mock
def test_402_without_an_envelope_reports_payment_error_type(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(402, text="Payment Required")
    )
    with pytest.raises(PaymentError) as exc_info:
        sync_client.customers.retrieve("cus_1")
    assert exc_info.value.type == "payment_error"


@pytest.mark.asyncio
@respx.mock
async def test_403_maps_to_permission_error(async_client: AsyncBillKit) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(
            403,
            json={"error": {"type": "permission_error", "message": "Insufficient scope."}},
        )
    )
    with pytest.raises(PermissionDeniedError):
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
    with pytest.raises(NotFoundError):
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
    assert exc_info.value.reason is None


@pytest.mark.asyncio
@respx.mock
async def test_409_carries_the_envelope_reason(async_client: AsyncBillKit) -> None:
    respx.post("https://test.billkit.eu/v1/subscriptions/sub_1/cancel").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": {
                    "type": "conflict_error",
                    "code": "operation_pending",
                    "reason": "renewal_processing",
                    "message": "A renewal payment is processing for this subscription.",
                }
            },
        )
    )
    with pytest.raises(ConflictError) as exc_info:
        await async_client.subscriptions.cancel("sub_1")
    assert exc_info.value.code == "operation_pending"
    assert exc_info.value.reason == "renewal_processing"


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
    # The proxy's page is kept as text: it is the one clue to what answered.
    assert exc_info.value.raw_body == "<html>bad gateway</html>"


# ── the status, not the envelope `type`, picks the class ──────────────
#
# An unmatched route is a 404 whose `type` is `invalid_request_error`
# (`route_not_found`): mapping on `type` would raise the wrong class. Before
# 1.0 the same miss read `{"type": "api_error", "code": "unhandled"}` *with
# the original 4xx status*, and a proxy in front of the API can still answer
# that way; mapping on `type` turned those into `ServerError`, telling the
# caller BillKit had broken when their own request was at fault, and
# `ServerError` is the class retry and alerting policies key on. Node and php
# assert the same cases.


@pytest.mark.asyncio
@respx.mock
async def test_404_route_not_found_is_not_found_not_invalid_request(
    async_client: AsyncBillKit,
) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(
            404,
            json={
                "error": {
                    "type": "invalid_request_error",
                    "code": "route_not_found",
                    "message": "Unrecognized request URL.",
                }
            },
        )
    )
    with pytest.raises(NotFoundError) as exc_info:
        await async_client.customers.retrieve("cus_1")
    assert not isinstance(exc_info.value, InvalidRequestError)
    assert exc_info.value.type == "invalid_request_error"
    assert exc_info.value.code == "route_not_found"


@pytest.mark.asyncio
@respx.mock
async def test_404_from_an_unmatched_route_is_not_a_server_error(
    async_client: AsyncBillKit,
) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(
            404,
            json={
                "error": {
                    "type": "api_error",
                    "code": "unhandled",
                    "message": "Not Found",
                }
            },
        )
    )
    with pytest.raises(NotFoundError) as exc_info:
        await async_client.customers.retrieve("cus_1")
    assert not isinstance(exc_info.value, ServerError)
    # The envelope value is still carried verbatim; it just does not choose
    # the class.
    assert exc_info.value.type == "api_error"
    assert exc_info.value.code == "unhandled"


@pytest.mark.asyncio
@respx.mock
async def test_405_method_not_allowed_is_an_invalid_request(
    async_client: AsyncBillKit,
) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(
            405,
            json={
                "error": {
                    "type": "invalid_request_error",
                    "code": "method_not_allowed",
                    "message": "nope",
                }
            },
        )
    )
    with pytest.raises(InvalidRequestError):
        await async_client.customers.retrieve("cus_1")


@pytest.mark.asyncio
@respx.mock
async def test_500_carrying_a_4xx_shaped_type_is_still_a_server_error(
    async_client: AsyncBillKit,
) -> None:
    """The mirror image: the status wins in both directions."""
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(
            500,
            json={"error": {"type": "invalid_request_error", "message": "mislabelled"}},
        )
    )
    with pytest.raises(ServerError):
        await async_client.customers.retrieve("cus_1")


# ── 1.0 hierarchy ──────────────────────────────────────────────────

_STATUS_CLASSES = [
    (400, "InvalidRequestError"),
    (401, "AuthenticationError"),
    (402, "PaymentError"),
    (403, "PermissionDeniedError"),
    (404, "NotFoundError"),
    (405, "InvalidRequestError"),
    (409, "ConflictError"),
    (413, "InvalidRequestError"),
    (422, "InvalidRequestError"),
    (429, "RateLimitError"),
    (500, "ServerError"),
    (503, "ServerError"),
]


@pytest.mark.parametrize(("status", "name"), _STATUS_CLASSES)
@respx.mock
def test_every_http_error_is_an_api_error(sync_client: BillKit, status: int, name: str) -> None:
    """In 0.x ``APIError`` meant "a 5xx". In 1.0 it is the parent of every
    HTTP error, so ``except APIError`` no longer singles out server faults."""
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(
            status,
            json={"error": {"type": "x", "code": "c", "message": "m", "param": "p"}},
            headers={"x-request-id": "req_1", "x-custom": "yes"},
        )
    )
    with pytest.raises(APIError) as exc_info:
        sync_client.customers.retrieve("cus_1", max_retries=0)
    err = exc_info.value
    assert type(err).__name__ == name
    assert isinstance(err, BillKitError)
    assert err.status_code == status
    assert (err.code, err.param, err.message, err.request_id) == ("c", "p", "m", "req_1")
    assert err.headers is not None
    assert err.headers["x-custom"] == "yes"
    assert err.raw_body == {"error": {"type": "x", "code": "c", "message": "m", "param": "p"}}


def test_hierarchy_names_and_parents() -> None:
    import billkit

    for name in (
        "InvalidRequestError",
        "AuthenticationError",
        "PaymentError",
        "PermissionDeniedError",
        "NotFoundError",
        "ConflictError",
        "RateLimitError",
        "ServerError",
    ):
        assert issubclass(getattr(billkit, name), billkit.APIError), name
    assert issubclass(billkit.APITimeoutError, billkit.APIConnectionError)
    assert not issubclass(billkit.APIConnectionError, billkit.APIError)
    assert issubclass(billkit.WebhookVerificationError, billkit.BillKitError)
    # The builtin is no longer shadowed, and the 0.x names are gone.
    assert not hasattr(billkit, "PermissionError")
    assert not hasattr(billkit, "ResourceMissingError")


def test_base_class_attributes_exist_on_every_error() -> None:
    """A handler typed against ``BillKitError`` can read the HTTP fields;
    they are ``None`` when there was no HTTP answer."""
    from billkit import APIConnectionError, WebhookVerificationError

    for err in (APIConnectionError("down"), WebhookVerificationError("bad")):
        assert err.status_code is None
        assert err.code is None
        assert err.request_id is None
        assert err.headers is None


@respx.mock
def test_envelope_less_errors_get_the_spec_type(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        side_effect=[httpx.Response(404), httpx.Response(409)]
    )
    with pytest.raises(NotFoundError) as not_found:
        sync_client.customers.retrieve("cus_1")
    assert not_found.value.type == "not_found_error"
    with pytest.raises(ConflictError) as conflict:
        sync_client.customers.retrieve("cus_1")
    assert conflict.value.type == "conflict_error"


# ── pickling ───────────────────────────────────────────────────────


def _raised_errors() -> list[BillKitError]:
    """One of every class, built the way the SDK builds them."""
    from billkit import APIConnectionError, APITimeoutError, WebhookVerificationError
    from billkit._errors import error_from_response

    out: list[BillKitError] = []
    for status in (400, 401, 403, 404, 409, 422, 429, 500, 503):
        out.append(
            error_from_response(
                status,
                {
                    "error": {
                        "type": "invalid_request_error",
                        "code": "parameter_invalid",
                        "message": f"boom {status}",
                        "param": "email",
                        "reason": "idempotency_key_blank",
                    }
                },
                request_id=f"req_{status}",
                retry_after=2.5 if status == 429 else None,
                headers=httpx.Headers({"X-Request-Id": f"req_{status}", "Retry-After": "2.5"}),
            )
        )
    out.append(APIConnectionError("could not connect"))
    out.append(APITimeoutError("timed out"))
    out.append(WebhookVerificationError("bad signature"))
    out.append(BillKitError("base"))
    return out


@pytest.mark.parametrize("error", _raised_errors(), ids=lambda e: type(e).__name__)
def test_every_error_round_trips_through_pickle(error: BillKitError) -> None:
    """``APIError`` takes ``status_code`` as a required keyword, so the
    default exception pickle (``cls(*args)``) could not rebuild it: an error
    could not cross a process boundary (multiprocessing, Celery)."""
    import pickle

    for protocol in range(2, pickle.HIGHEST_PROTOCOL + 1):
        back = pickle.loads(pickle.dumps(error, protocol=protocol))
        assert type(back) is type(error)
        assert back.args == error.args
        assert str(back) == str(error)
        assert repr(back) == repr(error)
        assert vars(back).keys() == vars(error).keys()
        for name in (
            "message",
            "status_code",
            "type",
            "code",
            "param",
            "reason",
            "request_id",
            "raw_body",
        ):
            assert getattr(back, name) == getattr(error, name), name
        if error.headers is not None:
            assert back.headers is not None
            assert dict(back.headers) == dict(error.headers)
        if isinstance(error, RateLimitError):
            assert isinstance(back, RateLimitError)
            assert back.retry_after == 2.5


def test_a_raised_error_pickles_with_its_traceback_dropped() -> None:
    import pickle

    try:
        raise RateLimitError("slow down", status_code=429, retry_after=1.0)
    except RateLimitError as exc:
        back = pickle.loads(pickle.dumps(exc))
    assert isinstance(back, RateLimitError)
    assert (back.status_code, back.retry_after, back.message) == (429, 1.0, "slow down")


# ── a 2xx the SDK cannot read ─────────────────────────────────────────
#
# A proxy or captive portal answering 200 with HTML used to come back as an
# empty object, so the caller carried on as if the call had worked.


def _html_200() -> httpx.Response:
    return httpx.Response(
        200,
        text="<html>Sign in to the hotel wifi</html>",
        headers={"X-Request-Id": "req_html"},
    )


def _assert_not_json_error(exc: APIError) -> None:
    assert type(exc) is APIError
    assert exc.status_code == 200
    assert exc.request_id == "req_html"
    assert exc.raw_body == "<html>Sign in to the hotel wifi</html>"
    assert exc.headers is not None
    assert exc.headers["x-request-id"] == "req_html"
    assert "not JSON" in exc.message


@pytest.mark.asyncio
@respx.mock
async def test_2xx_non_json_body_raises_api_error_async(async_client: AsyncBillKit) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(return_value=_html_200())
    with pytest.raises(APIError) as exc_info:
        await async_client.customers.retrieve("cus_1")
    _assert_not_json_error(exc_info.value)


@respx.mock
def test_2xx_non_json_body_raises_api_error_sync(sync_client: BillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(return_value=_html_200())
    with pytest.raises(APIError) as exc_info:
        sync_client.customers.create(email="a@b.co")
    _assert_not_json_error(exc_info.value)
    # The request did reach something and got a 2xx; it is not retried.
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_2xx_empty_body_is_an_empty_object_async(async_client: AsyncBillKit) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(200, content=b"")
    )
    customer = await async_client.customers.retrieve("cus_1")
    assert customer.to_dict() == {}


@respx.mock
def test_2xx_empty_body_is_an_empty_object_sync(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(return_value=httpx.Response(204))
    assert sync_client.customers.retrieve("cus_1").to_dict() == {}


@respx.mock
def test_4xx_non_json_body_keeps_text_sync(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(404, text="Not Found")
    )
    with pytest.raises(NotFoundError) as exc_info:
        sync_client.customers.retrieve("cus_1")
    assert exc_info.value.raw_body == "Not Found"


@respx.mock
def test_error_with_empty_body_has_no_raw_body_sync(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(return_value=httpx.Response(404))
    with pytest.raises(NotFoundError) as exc_info:
        sync_client.customers.retrieve("cus_1")
    assert exc_info.value.raw_body is None
