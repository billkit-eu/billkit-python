"""Per-request options, the client timeout with an injected httpx client,
and ``iter()`` carrying every ``list()`` filter onto every page."""

from __future__ import annotations

import inspect
import json
from typing import Any

import httpx
import pytest
import respx

from billkit import (
    NOT_GIVEN,
    APIConnectionError,
    APITimeoutError,
    AsyncBillKit,
    BillKit,
    NotGiven,
)
from tests.conftest import FAST_RETRY

CUSTOMER = {"id": "cus_1", "object": "customer"}


def _timeout_of(route: respx.Route) -> dict[str, float | None]:
    return route.calls.last.request.extensions["timeout"]  # type: ignore[no-any-return]


# ── timeout ────────────────────────────────────────────────────────


@respx.mock
def test_client_timeout_applies_through_an_injected_httpx_client() -> None:
    """0.x set the timeout only on a client it built itself, so with
    ``httpx_client=`` the caller's ``timeout=`` was silently ignored."""
    route = respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(200, json=CUSTOMER)
    )
    with httpx.Client(timeout=99.0) as injected:
        client = BillKit(
            api_key="bk_test_unit",
            base_url="https://test.billkit.eu",
            timeout=3.0,
            httpx_client=injected,
        )
        client.customers.retrieve("cus_1")
    assert _timeout_of(route) == httpx.Timeout(3.0).as_dict()


@respx.mock
async def test_async_client_timeout_applies_through_an_injected_httpx_client() -> None:
    route = respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(200, json=CUSTOMER)
    )
    async with httpx.AsyncClient(timeout=99.0) as injected:
        client = AsyncBillKit(
            api_key="bk_test_unit",
            base_url="https://test.billkit.eu",
            timeout=httpx.Timeout(4.0, connect=1.0),
            httpx_client=injected,
        )
        await client.customers.retrieve("cus_1")
    assert _timeout_of(route) == httpx.Timeout(4.0, connect=1.0).as_dict()


@respx.mock
def test_injected_client_keeps_its_own_timeout_when_none_is_given() -> None:
    route = respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(200, json=CUSTOMER)
    )
    with httpx.Client(timeout=42.0) as injected:
        BillKit(
            api_key="bk_test_unit", base_url="https://test.billkit.eu", httpx_client=injected
        ).customers.retrieve("cus_1")
    assert _timeout_of(route) == httpx.Timeout(42.0).as_dict()


@respx.mock
def test_default_timeout_on_an_owned_client(sync_client: BillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(200, json=CUSTOMER)
    )
    sync_client.customers.retrieve("cus_1")
    assert _timeout_of(route) == httpx.Timeout(30.0, connect=10.0).as_dict()


@respx.mock
def test_per_request_timeout_overrides_the_client(sync_client: BillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(200, json=CUSTOMER)
    )
    sync_client.customers.retrieve("cus_1", timeout=1.5)
    assert _timeout_of(route) == httpx.Timeout(1.5).as_dict()
    sync_client.customers.retrieve("cus_1", timeout=None)
    assert _timeout_of(route) == httpx.Timeout(None).as_dict()
    sync_client.customers.retrieve("cus_1")
    assert _timeout_of(route) == httpx.Timeout(30.0, connect=10.0).as_dict()


@respx.mock
def test_a_timeout_raises_api_timeout_error_after_the_budget(sync_client: BillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    with pytest.raises(APITimeoutError) as excinfo:
        sync_client.customers.retrieve("cus_1")
    assert isinstance(excinfo.value, APIConnectionError)
    assert route.call_count == FAST_RETRY.max_attempts


@respx.mock
async def test_a_connect_error_is_a_connection_error_not_a_timeout(
    async_client: AsyncBillKit,
) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        side_effect=httpx.ConnectError("refused")
    )
    with pytest.raises(APIConnectionError) as excinfo:
        await async_client.customers.retrieve("cus_1")
    assert not isinstance(excinfo.value, APITimeoutError)


# ── max_retries ────────────────────────────────────────────────────


@respx.mock
def test_max_retries_raises_the_budget_for_one_call(sync_client: BillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        side_effect=[httpx.Response(503)] * 5 + [httpx.Response(200, json=CUSTOMER)]
    )
    customer = sync_client.customers.create(email="a@b.co", max_retries=5)
    assert customer.id == "cus_1"
    assert route.call_count == 6
    keys = {call.request.headers["Idempotency-Key"] for call in route.calls}
    assert len(keys) == 1, "every attempt of one call carries the same key"


def test_negative_max_retries_is_refused_before_sending(sync_client: BillKit) -> None:
    with pytest.raises(ValueError, match="max_retries"):
        sync_client.customers.retrieve("cus_1", max_retries=-1)


# ── idempotency and headers ────────────────────────────────────────


@respx.mock
async def test_supplied_idempotency_key_is_reused_across_retries(
    async_client: AsyncBillKit,
) -> None:
    route = respx.post("https://test.billkit.eu/v1/subscriptions/sub_1/cancel").mock(
        side_effect=[httpx.Response(502), httpx.Response(200, json={"id": "sub_1"})]
    )
    await async_client.subscriptions.cancel(
        "sub_1", idempotency_key="cancel-at-period-end:sub_1", timeout=5.0
    )
    assert [c.request.headers["Idempotency-Key"] for c in route.calls] == [
        "cancel-at-period-end:sub_1"
    ] * 2


@respx.mock
def test_extra_headers_are_merged_over_the_defaults(sync_client: BillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(200, json=CUSTOMER)
    )
    sync_client.customers.retrieve("cus_1", extra_headers={"X-Trace": "abc"})
    request = route.calls.last.request
    assert request.headers["X-Trace"] == "abc"
    assert request.headers["Authorization"] == "Bearer bk_test_unit"


@pytest.mark.parametrize(
    "name",
    [
        "Authorization",
        "authorization",
        "AUTHORIZATION",
        "Idempotency-Key",
        "idempotency-key",
        "Content-Type",
        "user-agent",
    ],
)
@respx.mock
def test_extra_headers_cannot_set_the_sdks_own_headers(sync_client: BillKit, name: str) -> None:
    """In any case: a lower-case ``authorization`` went out beside the
    SDK's own, and an ``Idempotency-Key`` here bypassed the blank-key guard."""
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(200, json=CUSTOMER)
    )
    with pytest.raises(ValueError, match="extra_headers cannot set") as info:
        sync_client.customers.create(email="a@example.com", extra_headers={name: "x"})
    if name.lower() == "idempotency-key":
        assert "idempotency_key=" in str(info.value)
    assert not route.called


@respx.mock
def test_extra_headers_replace_case_insensitively_rather_than_duplicate(
    sync_client: BillKit,
) -> None:
    route = respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(200, json=CUSTOMER)
    )
    sync_client.customers.retrieve("cus_1", extra_headers={"accept": "application/json; v=1"})
    request = route.calls.last.request
    assert request.headers.get_list("Accept") == ["application/json; v=1"]
    assert request.headers.get_list("Authorization") == ["Bearer bk_test_unit"]


# ── extra_query / extra_body ───────────────────────────────────────


@respx.mock
def test_extra_query_sends_a_filter_the_sdk_has_no_keyword_for(sync_client: BillKit) -> None:
    """The escape hatch for a filter a later 1.x API adds: forwarded as
    given, merged over the keywords, with ``None`` dropped."""
    route = respx.get("https://test.billkit.eu/v1/customers").mock(return_value=_page([], False))
    sync_client.customers.list(
        limit=5,
        expand=["stats"],
        extra_query={"email": "ada@example.com", "limit": 50, "unset": None},
    )
    params = route.calls.last.request.url.params
    assert params["email"] == "ada@example.com"
    assert params["limit"] == "50"
    assert params["expand"] == "stats"
    assert "unset" not in params


@respx.mock
async def test_extra_body_merges_over_the_keywords_and_keeps_explicit_nulls(
    async_client: AsyncBillKit,
) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(200, json=CUSTOMER)
    )
    await async_client.customers.create(
        email="a@example.com",
        name="Ada",
        extra_body={"preferred_locales": ["nl"], "name": "Ada L.", "phone": None},
    )
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "email": "a@example.com",
        "name": "Ada L.",
        "preferred_locales": ["nl"],
        "phone": None,
    }


@respx.mock
def test_extra_body_on_a_bodyless_post_becomes_the_body(sync_client: BillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/subscriptions/sub_1/resume").mock(
        return_value=httpx.Response(200, json={"id": "sub_1"})
    )
    sync_client.subscriptions.resume("sub_1", extra_body={"proration": "none"})
    request = route.calls.last.request
    assert json.loads(request.content) == {"proration": "none"}
    assert request.headers["Content-Type"] == "application/json"


def test_extra_body_is_refused_on_a_read() -> None:
    """Reads do not take the keyword; the transport refuses it anyway, so
    a body can never ride on a GET through ``RequestOptions`` either."""
    from billkit._options import RequestOptions
    from billkit._transport import _merge_extras

    with pytest.raises(ValueError, match="GET"):
        _merge_extras("GET", None, None, RequestOptions(extra_body={"a": 1}))


# ── NOT_GIVEN ──────────────────────────────────────────────────────


def test_not_given_is_a_falsy_singleton() -> None:
    assert NotGiven() is NOT_GIVEN
    assert not NOT_GIVEN
    assert repr(NOT_GIVEN) == "NOT_GIVEN"


@respx.mock
def test_not_given_and_none_differ_on_a_clearable_field(sync_client: BillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/tenant/portal_branding").mock(
        return_value=httpx.Response(200, json={"object": "tenant_portal_branding"})
    )
    sync_client.tenant.set_portal_branding(business_name="Acme")
    assert route.calls.last.request.read() == b'{"business_name":"Acme"}'
    # 0.x dropped this None, which left no way to clear the field.
    sync_client.tenant.set_portal_branding(logo_url=None)
    assert route.calls.last.request.read() == b'{"logo_url":null}'


# ── iter() takes what list() takes ─────────────────────────────────

_PAIRS = {
    "list": "iter",
    "list_usage_records": "iter_usage_records",
    "list_deliveries": "iter_deliveries",
}


def test_every_iter_accepts_every_list_filter() -> None:
    """0.x ``iter()`` dropped ``expand`` on six resources and
    ``provisional`` on customers, so walking every page meant losing them."""
    client = BillKit(api_key="bk_test_unit")
    checked = 0
    for attr, resource in vars(client).items():
        if attr.startswith("_"):
            continue
        for list_name, iter_name in _PAIRS.items():
            if not hasattr(resource, list_name):
                continue
            list_params = set(inspect.signature(getattr(resource, list_name)).parameters)
            iter_params = set(inspect.signature(getattr(resource, iter_name)).parameters)
            assert list_params <= iter_params, (attr, list_params - iter_params)
            assert {"limit", "starting_after"} <= iter_params
            checked += 1
    client.close()
    assert checked == 19


def _page(ids: list[str], more: bool) -> httpx.Response:
    return httpx.Response(
        200,
        json={"object": "list", "data": [{"id": i} for i in ids], "has_more": more},
    )


@respx.mock
def test_iter_carries_filters_and_options_onto_every_page(sync_client: BillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/customers").mock(
        side_effect=[_page(["cus_1", "cus_2"], True), _page(["cus_3"], False)]
    )
    seen = [
        c.id
        for c in sync_client.customers.iter(
            provisional=False,
            expand=["stats"],
            limit=2,
            extra_headers={"X-Job": "export"},
            extra_query={"created_after": "1700000000"},
        )
    ]
    assert seen == ["cus_1", "cus_2", "cus_3"]
    first, second = (call.request for call in route.calls)
    for request in (first, second):
        assert request.url.params["provisional"] == "false"
        assert request.url.params["expand"] == "stats"
        assert request.url.params["limit"] == "2"
        assert request.headers["X-Job"] == "export"
        assert request.url.params["created_after"] == "1700000000"
    assert "starting_after" not in first.url.params
    assert second.url.params["starting_after"] == "cus_2"


@respx.mock
async def test_async_iter_carries_expand_and_resumes_from_a_cursor(
    async_client: AsyncBillKit,
) -> None:
    route = respx.get("https://test.billkit.eu/v1/subscriptions").mock(
        side_effect=[_page(["sub_2"], True), _page(["sub_3"], False)]
    )
    seen = [
        s.id
        async for s in async_client.subscriptions.iter(
            status="active", expand=["customer"], starting_after="sub_1"
        )
    ]
    assert seen == ["sub_2", "sub_3"]
    params: list[Any] = [call.request.url.params for call in route.calls]
    assert [p["starting_after"] for p in params] == ["sub_1", "sub_2"]
    assert all(p["expand"] == "customer" and p["status"] == "active" for p in params)


@respx.mock
def test_expand_as_a_bare_string_is_one_relation(sync_client: BillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/subscriptions/sub_1").mock(
        return_value=httpx.Response(200, json={"id": "sub_1"})
    )
    sync_client.subscriptions.retrieve("sub_1", expand="customer")
    assert route.calls.last.request.url.params["expand"] == "customer"
