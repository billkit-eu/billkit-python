"""Every public method, sync and async, against a stubbed API.

One table row per method. Each row is driven four ways (sync and async,
success and a refused retry) and asserts the same things of every method:

* the HTTP verb and path it sends;
* an ``Idempotency-Key`` on a mutating call and none on a read;
* the per-request options reach the wire: ``extra_headers`` as headers,
  ``extra_query`` in the query string, ``extra_body`` in a POST's body,
  ``timeout`` as the request's timeout, ``max_retries=0`` as one attempt;
* the return value is an instance of the method's declared return type.

``test_table_covers_every_public_method`` fails when a method is added
without a row, so this file cannot quietly fall behind the surface.
"""

from __future__ import annotations

import inspect
import json
import typing
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import respx

from billkit import AsyncBillKit, BillKit, BillKitObject, ListObject, ServerError, resources
from tests.conftest import FAST_RETRY

BASE = "https://test.billkit.eu"


@dataclass(frozen=True)
class Row:
    resource: str
    method: str
    http: str
    path: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.resource}.{self.method}"


TABLE: list[Row] = [
    # api_keys
    Row("api_keys", "create", "POST", "/v1/api_keys", kwargs={"label": "ci"}),
    Row("api_keys", "retrieve", "GET", "/v1/api_keys/ak_1", ("ak_1",)),
    Row("api_keys", "revoke", "POST", "/v1/api_keys/ak_1/revoke", ("ak_1",)),
    Row("api_keys", "list", "GET", "/v1/api_keys"),
    Row("api_keys", "iter", "GET", "/v1/api_keys"),
    # customers
    Row("customers", "create", "POST", "/v1/customers", kwargs={"email": "a@b.co"}),
    Row("customers", "retrieve", "GET", "/v1/customers/cus_1", ("cus_1",)),
    Row("customers", "update", "POST", "/v1/customers/cus_1", ("cus_1",), {"name": "Ada"}),
    Row("customers", "delete", "DELETE", "/v1/customers/cus_1", ("cus_1",)),
    Row("customers", "purge", "POST", "/v1/customers/cus_1/purge", ("cus_1",), {"confirmed": True}),
    Row(
        "customers",
        "set_vat_number",
        "POST",
        "/v1/customers/cus_1/vat_number",
        ("cus_1",),
        {"vat_number": "NL123"},
    ),
    Row("customers", "list", "GET", "/v1/customers"),
    Row("customers", "iter", "GET", "/v1/customers"),
    # products
    Row("products", "create", "POST", "/v1/products", kwargs={"name": "Pro"}),
    Row("products", "retrieve", "GET", "/v1/products/prod_1", ("prod_1",)),
    Row("products", "update", "POST", "/v1/products/prod_1", ("prod_1",), {"active": False}),
    Row("products", "list", "GET", "/v1/products"),
    Row("products", "iter", "GET", "/v1/products"),
    # prices
    Row(
        "prices",
        "create",
        "POST",
        "/v1/prices",
        kwargs={
            "product_id": "prod_1",
            "amount_cents": 900,
            "currency": "EUR",
            "interval": "month",
        },
    ),
    Row("prices", "retrieve", "GET", "/v1/prices/price_1", ("price_1",)),
    Row("prices", "update", "POST", "/v1/prices/price_1", ("price_1",), {"active": False}),
    Row("prices", "list", "GET", "/v1/prices"),
    Row("prices", "iter", "GET", "/v1/prices"),
    # checkout_sessions
    Row(
        "checkout_sessions",
        "create",
        "POST",
        "/v1/checkout/sessions",
        kwargs={
            "price_id": "price_1",
            "success_url": "https://m.example/ok",
            "cancel_url": "https://m.example/no",
        },
    ),
    Row("checkout_sessions", "retrieve", "GET", "/v1/checkout/sessions/cs_1", ("cs_1",)),
    # one_shot_payments
    Row(
        "one_shot_payments",
        "create",
        "POST",
        "/v1/checkout/one_shot",
        kwargs={
            "customer_id": "cus_1",
            "amount_cents": 2500,
            "currency": "EUR",
            "success_url": "https://m.example/ok",
            "payment_method": "ideal",
        },
    ),
    Row("one_shot_payments", "retrieve", "GET", "/v1/checkout/one_shot/osp_1", ("osp_1",)),
    Row("one_shot_payments", "list", "GET", "/v1/checkout/one_shot"),
    Row("one_shot_payments", "iter", "GET", "/v1/checkout/one_shot"),
    # subscriptions
    Row("subscriptions", "retrieve", "GET", "/v1/subscriptions/sub_1", ("sub_1",)),
    Row("subscriptions", "list", "GET", "/v1/subscriptions"),
    Row("subscriptions", "iter", "GET", "/v1/subscriptions"),
    Row("subscriptions", "cancel", "POST", "/v1/subscriptions/sub_1/cancel", ("sub_1",)),
    Row("subscriptions", "pause", "POST", "/v1/subscriptions/sub_1/pause", ("sub_1",)),
    Row("subscriptions", "resume", "POST", "/v1/subscriptions/sub_1/resume", ("sub_1",)),
    Row("subscriptions", "reactivate", "POST", "/v1/subscriptions/sub_1/reactivate", ("sub_1",)),
    Row(
        "subscriptions",
        "preview_update",
        "POST",
        "/v1/subscriptions/sub_1/preview_update",
        ("sub_1",),
        {"target_price_id": "price_2"},
    ),
    Row(
        "subscriptions",
        "update",
        "POST",
        "/v1/subscriptions/sub_1/update",
        ("sub_1",),
        {"target_price_id": "price_2"},
    ),
    Row(
        "subscriptions",
        "reauthorize_payment_method",
        "POST",
        "/v1/subscriptions/sub_1/reauthorize_payment_method",
        ("sub_1",),
        {"return_url": "https://m.example/back"},
    ),
    Row(
        "subscriptions",
        "create_usage_record",
        "POST",
        "/v1/subscriptions/sub_1/usage_records",
        ("sub_1",),
        {"quantity": 3},
    ),
    Row(
        "subscriptions",
        "list_usage_records",
        "GET",
        "/v1/subscriptions/sub_1/usage_records",
        ("sub_1",),
    ),
    Row(
        "subscriptions",
        "iter_usage_records",
        "GET",
        "/v1/subscriptions/sub_1/usage_records",
        ("sub_1",),
    ),
    Row(
        "subscriptions",
        "retrieve_usage_summary",
        "GET",
        "/v1/subscriptions/sub_1/usage_summary",
        ("sub_1",),
    ),
    # refunds
    Row("refunds", "create", "POST", "/v1/refunds", kwargs={"payment_id": "pay_1"}),
    Row("refunds", "retrieve", "GET", "/v1/refunds/re_1", ("re_1",)),
    Row("refunds", "list", "GET", "/v1/refunds"),
    Row("refunds", "iter", "GET", "/v1/refunds"),
    # disputes
    Row("disputes", "retrieve", "GET", "/v1/disputes/dp_1", ("dp_1",)),
    Row("disputes", "list", "GET", "/v1/disputes"),
    Row("disputes", "iter", "GET", "/v1/disputes"),
    # test_clocks
    Row("test_clocks", "create", "POST", "/v1/test_clocks", kwargs={"frozen_time": 1_700_000_000}),
    Row("test_clocks", "retrieve", "GET", "/v1/test_clocks/clock_1", ("clock_1",)),
    Row(
        "test_clocks",
        "advance",
        "POST",
        "/v1/test_clocks/clock_1/advance",
        ("clock_1",),
        {"frozen_time": 1_700_086_400},
    ),
    Row("test_clocks", "delete", "DELETE", "/v1/test_clocks/clock_1", ("clock_1",)),
    Row("test_clocks", "list", "GET", "/v1/test_clocks"),
    Row("test_clocks", "iter", "GET", "/v1/test_clocks"),
    # webhook_endpoints
    Row("webhook_endpoints", "list_event_types", "GET", "/v1/webhook_endpoints/event_types"),
    Row(
        "webhook_endpoints",
        "create",
        "POST",
        "/v1/webhook_endpoints",
        kwargs={"url": "https://m.example/hook", "enabled_events": ["*"]},
    ),
    Row("webhook_endpoints", "retrieve", "GET", "/v1/webhook_endpoints/we_1", ("we_1",)),
    Row(
        "webhook_endpoints",
        "update",
        "POST",
        "/v1/webhook_endpoints/we_1",
        ("we_1",),
        {"status": "disabled"},
    ),
    Row("webhook_endpoints", "delete", "DELETE", "/v1/webhook_endpoints/we_1", ("we_1",)),
    Row(
        "webhook_endpoints",
        "rotate_secret",
        "POST",
        "/v1/webhook_endpoints/we_1/rotate_secret",
        ("we_1",),
    ),
    Row("webhook_endpoints", "list", "GET", "/v1/webhook_endpoints"),
    Row("webhook_endpoints", "iter", "GET", "/v1/webhook_endpoints"),
    Row(
        "webhook_endpoints",
        "list_deliveries",
        "GET",
        "/v1/webhook_endpoints/we_1/deliveries",
        ("we_1",),
    ),
    Row(
        "webhook_endpoints",
        "iter_deliveries",
        "GET",
        "/v1/webhook_endpoints/we_1/deliveries",
        ("we_1",),
    ),
    Row(
        "webhook_endpoints",
        "retrieve_delivery",
        "GET",
        "/v1/webhook_endpoints/we_1/deliveries/wd_1",
        ("we_1", "wd_1"),
    ),
    Row(
        "webhook_endpoints",
        "redeliver",
        "POST",
        "/v1/webhook_endpoints/we_1/deliveries/wd_1/redeliver",
        ("we_1", "wd_1"),
    ),
    # events
    Row("events", "retrieve", "GET", "/v1/events/evt_1", ("evt_1",)),
    Row("events", "list", "GET", "/v1/events"),
    Row("events", "iter", "GET", "/v1/events"),
    # tenant
    Row("tenant", "retrieve_capabilities", "GET", "/v1/tenant/capabilities"),
    Row("tenant", "retrieve_portal_branding", "GET", "/v1/tenant/portal_branding"),
    Row(
        "tenant",
        "set_portal_branding",
        "POST",
        "/v1/tenant/portal_branding",
        kwargs={"business_name": "Acme"},
    ),
    Row("tenant", "retrieve_billing_profile", "GET", "/v1/tenant/billing_profile"),
    Row(
        "tenant",
        "set_billing_profile",
        "POST",
        "/v1/tenant/billing_profile",
        kwargs={"country_code": "NL"},
    ),
    Row("tenant", "export", "GET", "/v1/tenant/export"),
    Row(
        "tenant",
        "rotate_provider_credential",
        "POST",
        "/v1/tenant/provider_credential",
        kwargs={"api_key": "test_abcdefgh"},
    ),
    # coupons
    Row("coupons", "create", "POST", "/v1/coupons", kwargs={"code": "TEN", "percent_off": 10}),
    Row("coupons", "retrieve", "GET", "/v1/coupons/co_1", ("co_1",)),
    Row("coupons", "update", "POST", "/v1/coupons/co_1", ("co_1",), {"active": False}),
    Row("coupons", "validate", "POST", "/v1/coupons/validate", kwargs={"code": "TEN"}),
    Row("coupons", "list", "GET", "/v1/coupons"),
    Row("coupons", "iter", "GET", "/v1/coupons"),
    # tax_rates
    Row(
        "tax_rates",
        "create",
        "POST",
        "/v1/tax_rates",
        kwargs={"country_code": "NL", "rate_basis_points": 2100},
    ),
    Row("tax_rates", "retrieve", "GET", "/v1/tax_rates/txr_1", ("txr_1",)),
    Row("tax_rates", "update", "POST", "/v1/tax_rates/txr_1", ("txr_1",), {"active": False}),
    Row("tax_rates", "list", "GET", "/v1/tax_rates"),
    Row("tax_rates", "iter", "GET", "/v1/tax_rates"),
    # invoices
    Row("invoices", "retrieve", "GET", "/v1/invoices/in_1", ("in_1",)),
    Row("invoices", "retrieve_pdf", "GET", "/v1/invoices/in_1/pdf", ("in_1",)),
    Row("invoices", "send_email", "POST", "/v1/invoices/in_1/email", ("in_1",)),
    Row("invoices", "void", "POST", "/v1/invoices/in_1/void", ("in_1",), {"reason": "dup"}),
    Row("invoices", "list", "GET", "/v1/invoices"),
    Row("invoices", "iter", "GET", "/v1/invoices"),
    # credit_notes
    Row("credit_notes", "retrieve", "GET", "/v1/credit_notes/cn_1", ("cn_1",)),
    Row("credit_notes", "retrieve_pdf", "GET", "/v1/credit_notes/cn_1/pdf", ("cn_1",)),
    Row("credit_notes", "list", "GET", "/v1/credit_notes"),
    Row("credit_notes", "iter", "GET", "/v1/credit_notes"),
    # audit_logs
    Row("audit_logs", "retrieve", "GET", "/v1/audit_logs/al_1", ("al_1",)),
    Row("audit_logs", "list", "GET", "/v1/audit_logs"),
    Row("audit_logs", "iter", "GET", "/v1/audit_logs"),
    # payments
    Row("payments", "retrieve", "GET", "/v1/payments/pay_1", ("pay_1",)),
    Row("payments", "retrieve_provider", "GET", "/v1/payments/pay_1/provider", ("pay_1",)),
    Row("payments", "list", "GET", "/v1/payments"),
    Row("payments", "iter", "GET", "/v1/payments"),
    # billing_portal_sessions
    Row(
        "billing_portal_sessions",
        "create",
        "POST",
        "/v1/billing_portal/sessions",
        kwargs={"subscription_id": "sub_1", "return_url": "https://m.example/back"},
    ),
    Row(
        "billing_portal_sessions",
        "revoke",
        "POST",
        "/v1/billing_portal/sessions/bps_1/revoke",
        ("bps_1",),
    ),
]

_ROWS = {row.id: row for row in TABLE}
_LIST_BODY = {"object": "list", "data": [{"id": "obj_1", "object": "thing"}], "has_more": False}
_OBJECT_BODY = {"id": "obj_1", "object": "thing", "a_field_from_the_future": {"nested": True}}
_BYTES_ROUTES = {"invoices.retrieve_pdf", "credit_notes.retrieve_pdf", "tenant.export"}


def _is_iter(row: Row) -> bool:
    return row.method.startswith("iter")


def _response(row: Row) -> httpx.Response:
    if row.id in _BYTES_ROUTES:
        return httpx.Response(200, content=b"%PDF-1.7 bytes")
    if _is_iter(row) or (row.method.startswith("list") and row.method != "list_event_types"):
        return httpx.Response(200, json=_LIST_BODY)
    return httpx.Response(200, json=_OBJECT_BODY)


def _expected_type(resource_cls: type, method: str) -> Any:
    hints = typing.get_type_hints(getattr(resource_cls, method), vars(resources))
    return hints["return"]


def _check_result(row: Row, resource_cls: type, result: Any) -> None:
    expected = _expected_type(resource_cls, row.method)
    origin = typing.get_origin(expected)
    if origin in (Iterator, AsyncIterator):
        (item_type,) = typing.get_args(expected)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], item_type)
        assert result[0]["id"] == "obj_1"
    elif expected is bytes:
        assert result == b"%PDF-1.7 bytes"
    else:
        cls = origin or expected
        assert isinstance(result, cls), (row.id, type(result), expected)
        assert isinstance(result, (BillKitObject, ListObject))
        if isinstance(result, ListObject):
            assert [item["id"] for item in result] == ["obj_1"]
        elif row.method != "list_event_types":
            # A field this SDK has never heard of survives, both ways.
            assert result["a_field_from_the_future"] == {"nested": True}
            assert result.to_dict()["a_field_from_the_future"] == {"nested": True}


def _check_request(row: Row, request: httpx.Request) -> None:
    assert request.method == row.http
    assert request.url.path == row.path
    if row.http == "GET":
        assert "Idempotency-Key" not in request.headers
    else:
        assert request.headers["Idempotency-Key"].startswith("sdk-")
    assert request.headers["X-Surface-Test"] == row.id
    assert request.extensions["timeout"] == httpx.Timeout(12.5).as_dict()
    assert request.url.params["x_surface_test"] == row.id
    if row.http == "POST":
        assert json.loads(request.content)["x_surface_test"] == row.id


def _options(row: Row) -> dict[str, Any]:
    options: dict[str, Any] = {
        "timeout": 12.5,
        "extra_headers": {"X-Surface-Test": row.id},
        "extra_query": {"x_surface_test": row.id},
    }
    if row.http == "POST":
        options["extra_body"] = {"x_surface_test": row.id}
    return options


def _sync_call(client: BillKit, row: Row, **options: Any) -> Any:
    method = getattr(getattr(client, row.resource), row.method)
    result = method(*row.args, **row.kwargs, **options)
    return list(result) if _is_iter(row) else result


async def _async_call(client: AsyncBillKit, row: Row, **options: Any) -> Any:
    method = getattr(getattr(client, row.resource), row.method)
    if _is_iter(row):
        return [item async for item in method(*row.args, **row.kwargs, **options)]
    return await method(*row.args, **row.kwargs, **options)


@pytest.mark.parametrize("row", TABLE, ids=[r.id for r in TABLE])
@respx.mock
def test_sync_method(row: Row, sync_client: BillKit) -> None:
    route = respx.route(method=row.http, url=BASE + row.path).mock(return_value=_response(row))
    result = _sync_call(sync_client, row, **_options(row))
    assert route.call_count == 1
    _check_request(row, route.calls.last.request)
    _check_result(row, type(getattr(sync_client, row.resource)), result)


@pytest.mark.parametrize("row", TABLE, ids=[r.id for r in TABLE])
@respx.mock
async def test_async_method(row: Row, async_client: AsyncBillKit) -> None:
    route = respx.route(method=row.http, url=BASE + row.path).mock(return_value=_response(row))
    result = await _async_call(async_client, row, **_options(row))
    assert route.call_count == 1
    _check_request(row, route.calls.last.request)
    _check_result(row, type(getattr(async_client, row.resource)), result)


_FAILING = httpx.Response(503, json={"error": {"type": "api_error", "message": "down"}})


@pytest.mark.parametrize("row", TABLE, ids=[r.id for r in TABLE])
@respx.mock
def test_sync_max_retries_zero_is_one_attempt(row: Row, sync_client: BillKit) -> None:
    """The client allows three attempts; ``max_retries=0`` on the call wins."""
    route = respx.route(method=row.http, url=BASE + row.path).mock(return_value=_FAILING)
    with pytest.raises(ServerError):
        _sync_call(sync_client, row, max_retries=0)
    assert route.call_count == 1


@pytest.mark.parametrize("row", TABLE, ids=[r.id for r in TABLE])
@respx.mock
async def test_async_max_retries_zero_is_one_attempt(row: Row, async_client: AsyncBillKit) -> None:
    route = respx.route(method=row.http, url=BASE + row.path).mock(return_value=_FAILING)
    with pytest.raises(ServerError):
        await _async_call(async_client, row, max_retries=0)
    assert route.call_count == 1


def _public_methods(resource: object) -> set[str]:
    return {
        name
        for name, _ in inspect.getmembers(type(resource), inspect.isfunction)
        if not name.startswith("_")
    }


def test_table_covers_every_public_method() -> None:
    sync = BillKit(api_key="bk_test_unit", retry_policy=FAST_RETRY)
    asynchronous = AsyncBillKit(api_key="bk_test_unit", retry_policy=FAST_RETRY)
    surface: set[str] = set()
    for client in (sync, asynchronous):
        for attr, resource in vars(client).items():
            if attr.startswith("_"):
                continue
            surface |= {f"{attr}.{m}" for m in _public_methods(resource)}
    sync.close()
    assert surface == set(_ROWS), {
        "untested": sorted(surface - set(_ROWS)),
        "stale rows": sorted(set(_ROWS) - surface),
    }


def test_every_method_takes_the_request_options() -> None:
    """``timeout``, ``max_retries``, ``extra_headers`` and ``extra_query`` on
    every method, keyword-only; ``idempotency_key`` on exactly the mutating
    ones, ``extra_body`` on exactly the POSTs."""
    client = BillKit(api_key="bk_test_unit")
    for row in TABLE:
        params = inspect.signature(getattr(getattr(client, row.resource), row.method)).parameters
        for name in ("timeout", "max_retries", "extra_headers", "extra_query"):
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, (row.id, name)
        assert ("extra_body" in params) == (row.http == "POST"), row.id
        has_key = "idempotency_key" in params
        assert has_key == (row.http != "GET"), row.id
        if has_key:
            assert params["idempotency_key"].kind is inspect.Parameter.KEYWORD_ONLY
    client.close()
