"""Smoke coverage for the six resource families added after the
initial v0.1 ship: Coupons, TaxRates, Invoices, AuditLogs, Payments,
BillingPortalSessions.

The transport, auth header, and idempotency-key wiring are already
covered by ``test_happy_path``; these tests only verify that each
resource hits the right path with the right body / params shape."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from billkit import AsyncBillKit, BillKit
from tests.conftest import assert_idempotency_header, last_request_body

# ─── Coupons ──────────────────────────────────────────────────────


@respx.mock
def test_coupon_create_and_validate(sync_client: BillKit) -> None:
    create = respx.post("https://test.billkit.eu/v1/coupons").mock(
        return_value=httpx.Response(200, json={"id": "coup_1", "object": "coupon", "code": "PROMO"})
    )
    validate = respx.post("https://test.billkit.eu/v1/coupons/validate").mock(
        return_value=httpx.Response(200, json={"valid": True, "discount_cents": 200})
    )
    coupon = sync_client.coupons.create(
        code="PROMO", discount_type="percent", discount_value=10, duration="once"
    )
    preview = sync_client.coupons.validate(code="PROMO", price_id="price_1", amount_cents=999)
    assert coupon["code"] == "PROMO"
    assert preview["valid"] is True
    assert create.called
    assert validate.called


@respx.mock
def test_coupon_list_iter(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/coupons").mock(
        return_value=httpx.Response(
            200, json={"object": "list", "data": [{"id": "coup_1"}], "has_more": False}
        )
    )
    assert [c["id"] for c in sync_client.coupons.iter()] == ["coup_1"]


# ─── TaxRates ─────────────────────────────────────────────────────


@respx.mock
def test_tax_rate_create(sync_client: BillKit) -> None:
    respx.post("https://test.billkit.eu/v1/tax_rates").mock(
        return_value=httpx.Response(
            200, json={"id": "tr_1", "country_code": "NL", "rate_basis_points": 2100}
        )
    )
    rate = sync_client.tax_rates.create(country_code="NL", rate_basis_points=2100)
    assert rate["country_code"] == "NL"


# ─── Invoices ─────────────────────────────────────────────────────


@respx.mock
def test_invoice_retrieve_and_list(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/invoices/inv_1").mock(
        return_value=httpx.Response(
            200, json={"id": "inv_1", "object": "invoice", "total_cents": 999}
        )
    )
    respx.get("https://test.billkit.eu/v1/invoices").mock(
        return_value=httpx.Response(
            200, json={"object": "list", "data": [{"id": "inv_1"}], "has_more": False}
        )
    )
    invoice = sync_client.invoices.retrieve("inv_1")
    rows = list(sync_client.invoices.iter())
    assert invoice["total_cents"] == 999
    assert len(rows) == 1


# ─── AuditLogs ────────────────────────────────────────────────────


@respx.mock
def test_audit_logs_list_forwards_filters(sync_client: BillKit) -> None:
    """The ``action`` filter must reach the server as a query param,
    not just disappear into the SDK."""
    route = respx.get("https://test.billkit.eu/v1/audit_logs").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [], "has_more": False})
    )
    sync_client.audit_logs.list(action="customer.created", resource_type="customer")
    assert route.called
    params = route.calls.last.request.url.params
    assert params["action"] == "customer.created"
    assert params["resource_type"] == "customer"


@respx.mock
def test_audit_logs_iter_forwards_filters(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/audit_logs").mock(
        return_value=httpx.Response(
            200, json={"object": "list", "data": [{"id": "log_1"}], "has_more": False}
        )
    )
    rows = list(sync_client.audit_logs.iter(action="customer.created"))
    assert len(rows) == 1


# ─── Payments ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_payments_retrieve_async(async_client: AsyncBillKit) -> None:
    respx.get("https://test.billkit.eu/v1/payments/pay_1").mock(
        return_value=httpx.Response(
            200, json={"id": "pay_1", "object": "payment", "status": "paid"}
        )
    )
    payment = await async_client.payments.retrieve("pay_1")
    assert payment["status"] == "paid"


# ─── BillingPortalSessions ────────────────────────────────────────


@respx.mock
def test_billing_portal_session_create(sync_client: BillKit) -> None:
    """Mint sends subscription_id + return_url; response includes
    the token surfaced once."""
    route = respx.post("https://test.billkit.eu/v1/billing_portal/sessions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "bps_1",
                "object": "billing_portal.session",
                "token": "bk_portal_test_abc",
                "url": "https://portal.billkit.eu/bk_portal_test_abc",
                "expires_at": 0,
            },
        )
    )
    session = sync_client.billing_portal_sessions.create(
        subscription_id="sub_1", return_url="https://example.com/back"
    )
    assert session["token"].startswith("bk_portal_")
    body = route.calls.last.request.read().decode()
    assert "sub_1" in body
    assert "https://example.com/back" in body


@respx.mock
def test_billing_portal_session_revoke(sync_client: BillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/billing_portal/sessions/bps_1/revoke").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "bps_1",
                "object": "billing_portal.session",
                "revoked_at": 0,
            },
        )
    )
    resp = sync_client.billing_portal_sessions.revoke("bps_1")
    assert resp["id"] == "bps_1"
    assert route.called


# ─── Customers: VAT + GDPR purge ──────────────────────────────────


@respx.mock
def test_customers_create_does_not_send_vat_number(sync_client: BillKit) -> None:
    """``vat_number`` lives at a separate endpoint; including it in
    ``POST /v1/customers`` would 422 against the ``extra=forbid``
    schema. Regression guard for the 0.1 → 0.2 contract fix."""
    route = respx.post("https://test.billkit.eu/v1/customers").mock(
        return_value=httpx.Response(
            200, json={"id": "cus_1", "object": "customer", "vat_number": None}
        )
    )
    sync_client.customers.create(email="ada@example.com", name="Ada", country_code="NL")
    body = last_request_body(route)
    assert "vat_number" not in body
    assert body["email"] == "ada@example.com"


@respx.mock
def test_customers_set_vat_number(sync_client: BillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/customers/cus_1/vat_number").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "cus_1",
                "object": "customer",
                "vat_number": "NL123456789B01",
                "vat_number_validated": True,
            },
        )
    )
    result = sync_client.customers.set_vat_number(
        "cus_1", vat_number="NL123456789B01", country_code="NL"
    )
    assert result["vat_number"] == "NL123456789B01"
    assert route.called


@respx.mock
def test_customers_purge_defaults_confirmed_true(sync_client: BillKit) -> None:
    """SDK defaults ``confirmed=True`` so the caller doesn't accidentally
    no-op against the server's fat-finger guard."""
    route = respx.post("https://test.billkit.eu/v1/customers/cus_1/purge").mock(
        return_value=httpx.Response(
            200,
            json={"id": "cus_1", "object": "customer", "purged_at": 0},
        )
    )
    sync_client.customers.purge("cus_1")
    body = last_request_body(route)
    assert body == {"confirmed": True}


# ─── CheckoutSessions: fixed body shape ───────────────────────────


@respx.mock
def test_checkout_session_create_uses_method_and_coupon_code(sync_client: BillKit) -> None:
    """0.1 sent ``payment_method``/``coupon`` which the API rejects.
    0.2 sends ``method``/``coupon_code``/``trial_days_override`` matching
    ``CheckoutSessionCreate``.

    ``metadata`` is a supported field again (the API gained it), but it must
    still be **absent** from the body when the caller didn't pass one:
    ``CheckoutSessionCreate`` is ``extra="forbid"`` and an explicit ``null``
    is not the same as omission."""
    route = respx.post("https://test.billkit.eu/v1/checkout/sessions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "cs_1",
                "object": "checkout_session",
                "url": "https://checkout.mollie.com/cs_1",
            },
        )
    )
    sync_client.checkout_sessions.create(
        customer_id="cus_1",
        price_id="price_1",
        success_url="https://app.example.com/ok",
        cancel_url="https://app.example.com/no",
        method="creditcard",
        coupon_code="LAUNCH50",
        trial_days_override=7,
    )
    body = last_request_body(route)
    assert body["method"] == "creditcard"
    assert body["coupon_code"] == "LAUNCH50"
    assert body["trial_days_override"] == 7
    # Regression: the 0.1 names must not slip back in.
    assert "payment_method" not in body
    assert "coupon" not in body
    assert "metadata" not in body


@respx.mock
def test_checkout_session_create_embedded_sends_ui_mode_and_metadata(
    sync_client: BillKit,
) -> None:
    """Embedded checkout: ``ui_mode`` selects the element surface and
    ``metadata`` carries the caller's own correlation id through to the
    ``checkout.session.completed`` webhook."""
    route = respx.post("https://test.billkit.eu/v1/checkout/sessions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "cs_1",
                "object": "checkout_session",
                "ui_mode": "embedded",
                "url": None,
                "client_secret": "cs_1_secret_abc",
                "metadata": {"family_id": "fam_42"},
            },
        )
    )
    session = sync_client.checkout_sessions.create(
        customer_email="ada@example.com",
        price_id="price_1",
        success_url="https://app.example.com/ok",
        cancel_url="https://app.example.com/no",
        ui_mode="embedded",
        metadata={"family_id": "fam_42"},
    )
    body = last_request_body(route)
    assert body["ui_mode"] == "embedded"
    assert body["metadata"] == {"family_id": "fam_42"}
    # Embedded sessions choose the method inside the element, so the SDK
    # must not smuggle one in; the API 422s the combination.
    assert "method" not in body
    assert session["client_secret"] == "cs_1_secret_abc"
    assert session["metadata"] == {"family_id": "fam_42"}


@pytest.mark.asyncio
@respx.mock
async def test_async_checkout_session_create_embedded(async_client: AsyncBillKit) -> None:
    """The async flavour carries the same two fields; the two surfaces
    are meant to be signature-identical."""
    route = respx.post("https://test.billkit.eu/v1/checkout/sessions").mock(
        return_value=httpx.Response(
            200,
            json={"id": "cs_1", "object": "checkout_session", "client_secret": "cs_1_secret_abc"},
        )
    )
    await async_client.checkout_sessions.create(
        customer_id="cus_1",
        price_id="price_1",
        success_url="https://app.example.com/ok",
        cancel_url="https://app.example.com/no",
        ui_mode="embedded",
        metadata={"family_id": "fam_42"},
    )
    body = last_request_body(route)
    assert body["ui_mode"] == "embedded"
    assert body["metadata"] == {"family_id": "fam_42"}


@respx.mock
def test_checkout_session_create_with_customer_email(sync_client: BillKit) -> None:
    """Stripe-compat shortcut: pass ``customer_email`` instead of
    ``customer_id`` and the body must carry it through verbatim."""
    route = respx.post("https://test.billkit.eu/v1/checkout/sessions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "cs_1",
                "object": "checkout_session",
                "customer_id": "cus_freshly_created",
                "url": "https://checkout.mollie.com/cs_1",
            },
        )
    )
    sync_client.checkout_sessions.create(
        customer_email="ada@example.com",
        customer_name="Ada Lovelace",
        price_id="price_1",
        success_url="https://app.example.com/ok",
        cancel_url="https://app.example.com/no",
    )
    body = last_request_body(route)
    assert body["customer_email"] == "ada@example.com"
    assert body["customer_name"] == "Ada Lovelace"
    assert "customer_id" not in body


# ─── Subscriptions: reactivate ────────────────────────────────────


@respx.mock
def test_subscription_reactivate_sync(sync_client: BillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/subscriptions/sub_1/reactivate").mock(
        return_value=httpx.Response(
            200, json={"id": "sub_1", "object": "subscription", "status": "active"}
        )
    )
    result = sync_client.subscriptions.reactivate("sub_1")
    assert result["status"] == "active"
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_subscription_reactivate_async(async_client: AsyncBillKit) -> None:
    respx.post("https://test.billkit.eu/v1/subscriptions/sub_1/reactivate").mock(
        return_value=httpx.Response(
            200, json={"id": "sub_1", "object": "subscription", "status": "active"}
        )
    )
    result = await async_client.subscriptions.reactivate("sub_1")
    assert result["status"] == "active"


# ─── OneShotPayments ──────────────────────────────────────────────


@respx.mock
def test_one_shot_create_sync(sync_client: BillKit) -> None:
    """Create posts to /v1/checkout/one_shot with the charge fields.
    ``refund_window_days=0`` survives ``_drop_none`` (disables refunds);
    an omitted ``cancel_url`` is dropped, not sent as null."""
    route = respx.post("https://test.billkit.eu/v1/checkout/one_shot").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "osp_1",
                "object": "one_shot_payment",
                "status": "open",
                "redirect_url": "https://www.mollie.com/checkout/osp_1",
            },
        )
    )
    payment = sync_client.one_shot_payments.create(
        customer_id="cus_1",
        amount_cents=2500,
        currency="EUR",
        method="ideal",
        success_url="https://shop.example.com/thanks",
        refund_window_days=0,
    )
    assert payment["status"] == "open"
    body = last_request_body(route)
    assert body["customer_id"] == "cus_1"
    assert body["amount_cents"] == 2500
    assert body["method"] == "ideal"
    assert body["refund_window_days"] == 0
    assert "cancel_url" not in body


@respx.mock
def test_one_shot_retrieve_sync(sync_client: BillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/checkout/one_shot/osp_1").mock(
        return_value=httpx.Response(200, json={"id": "osp_1", "object": "one_shot_payment"})
    )
    payment = sync_client.one_shot_payments.retrieve("osp_1")
    assert payment["id"] == "osp_1"
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_one_shot_create_async(async_client: AsyncBillKit) -> None:
    respx.post("https://test.billkit.eu/v1/checkout/one_shot").mock(
        return_value=httpx.Response(
            200, json={"id": "osp_1", "object": "one_shot_payment", "status": "open"}
        )
    )
    payment = await async_client.one_shot_payments.create(
        customer_id="cus_1",
        amount_cents=1000,
        currency="EUR",
        method="creditcard",
        success_url="https://shop.example.com/thanks",
    )
    assert payment["status"] == "open"


@respx.mock
def test_refund_create_one_shot_target(sync_client: BillKit) -> None:
    """A refund can target a one-shot via ``one_shot_payment_id``; the
    unused ``payment_id`` / ``subscription_id`` are dropped from the body."""
    route = respx.post("https://test.billkit.eu/v1/refunds").mock(
        return_value=httpx.Response(
            200,
            json={"id": "re_1", "object": "refund", "one_shot_payment_id": "osp_1"},
        )
    )
    refund = sync_client.refunds.create(one_shot_payment_id="osp_1", reason="changed mind")
    assert refund["one_shot_payment_id"] == "osp_1"
    body = last_request_body(route)
    assert body["one_shot_payment_id"] == "osp_1"
    assert body["reason"] == "changed mind"
    assert "payment_id" not in body
    assert "subscription_id" not in body


@respx.mock
def test_dispute_retrieve(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/disputes/dp_1").mock(
        return_value=httpx.Response(200, json={"id": "dp_1", "object": "dispute", "status": "open"})
    )
    dispute = sync_client.disputes.retrieve("dp_1")
    assert dispute["id"] == "dp_1"
    assert dispute["status"] == "open"


@respx.mock
def test_dispute_list(sync_client: BillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/disputes").mock(
        return_value=httpx.Response(
            200, json={"object": "list", "data": [{"id": "dp_1"}], "has_more": False}
        )
    )
    page = sync_client.disputes.list(limit=5)
    assert page["data"][0]["id"] == "dp_1"
    assert route.calls.last.request.url.params["limit"] == "5"


# ─── iter consistency: Products / Prices / Refunds ────────────────


@respx.mock
def test_products_iter(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/products").mock(
        return_value=httpx.Response(
            200,
            json={"object": "list", "data": [{"id": "prod_1"}], "has_more": False},
        )
    )
    assert [p["id"] for p in sync_client.products.iter()] == ["prod_1"]


@respx.mock
def test_prices_iter(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/prices").mock(
        return_value=httpx.Response(
            200,
            json={"object": "list", "data": [{"id": "price_1"}], "has_more": False},
        )
    )
    assert [p["id"] for p in sync_client.prices.iter()] == ["price_1"]


@respx.mock
def test_refunds_iter(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/refunds").mock(
        return_value=httpx.Response(
            200,
            json={"object": "list", "data": [{"id": "re_1"}], "has_more": False},
        )
    )
    assert [r["id"] for r in sync_client.refunds.iter()] == ["re_1"]


# ─── Prices: refund window overrides ──────────────────────────────


@respx.mock
def test_prices_create_with_refund_window_override(sync_client: BillKit) -> None:
    """Per-price refund window overrides round-trip on the create body.
    ``0`` survives ``_drop_none`` (only ``None`` is dropped) so the
    "refunds disabled for this charge type" signal reaches the API."""
    route = respx.post("https://test.billkit.eu/v1/prices").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "price_1",
                "object": "price",
                "refund_window_initial_days": 14,
                "refund_window_renewal_days": 0,
            },
        )
    )
    sync_client.prices.create(
        product_id="prod_bundle",
        amount_cents=1499,
        currency="EUR",
        interval="month",
        refund_window_initial_days=14,
        refund_window_renewal_days=0,
    )
    body = last_request_body(route)
    assert body["refund_window_initial_days"] == 14
    assert body["refund_window_renewal_days"] == 0


@respx.mock
def test_prices_create_omits_refund_window_when_none(sync_client: BillKit) -> None:
    """When the caller passes nothing, the body omits both keys so the
    server applies the default policy table rather than seeing an
    explicit-null that the schema would currently accept anyway."""
    route = respx.post("https://test.billkit.eu/v1/prices").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "price_1",
                "object": "price",
                "refund_window_initial_days": None,
                "refund_window_renewal_days": None,
            },
        )
    )
    sync_client.prices.create(
        product_id="prod_1",
        amount_cents=999,
        currency="EUR",
        interval="month",
    )
    body = last_request_body(route)
    assert "refund_window_initial_days" not in body
    assert "refund_window_renewal_days" not in body


# ─── Usage records (metered billing) ──────────────────────────────


@respx.mock
def test_usage_record_create_sync(sync_client: BillKit) -> None:
    """Create posts to the nested route with quantity/occurred_at/metadata;
    omitted optionals are dropped, not sent as null."""
    route = respx.post("https://test.billkit.eu/v1/subscriptions/sub_1/usage_records").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "ur_1",
                "object": "usage_record",
                "subscription_id": "sub_1",
                "quantity": 42,
                "invoice_id": None,
            },
        )
    )
    record = sync_client.subscriptions.create_usage_record(
        "sub_1", quantity=42, occurred_at=1_700_000_000, metadata={"source": "unit"}
    )
    assert record["object"] == "usage_record"
    body = last_request_body(route)
    assert body == {
        "quantity": 42,
        "occurred_at": 1_700_000_000,
        "metadata": {"source": "unit"},
    }


@respx.mock
def test_usage_record_create_minimal_body_and_idempotency(sync_client: BillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/subscriptions/sub_1/usage_records").mock(
        return_value=httpx.Response(201, json={"id": "ur_1", "object": "usage_record"})
    )
    sync_client.subscriptions.create_usage_record("sub_1", quantity=1, idempotency_key="usage-1")
    body = last_request_body(route)
    assert body == {"quantity": 1}
    assert route.calls.last.request.headers["Idempotency-Key"] == "usage-1"


@respx.mock
def test_usage_record_list_sync(sync_client: BillKit) -> None:
    """List GETs the nested route and forwards the invoice_id filter."""
    route = respx.get(
        "https://test.billkit.eu/v1/subscriptions/sub_1/usage_records",
        params={"invoice_id": "pending", "limit": "25"},
    ).mock(
        return_value=httpx.Response(
            200, json={"object": "list", "data": [{"id": "ur_1"}], "has_more": False}
        )
    )
    page = sync_client.subscriptions.list_usage_records("sub_1", invoice_id="pending", limit=25)
    assert page["data"][0]["id"] == "ur_1"
    assert route.called


@respx.mock
def test_usage_record_iter_sync(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/subscriptions/sub_1/usage_records").mock(
        return_value=httpx.Response(
            200, json={"object": "list", "data": [{"id": "ur_1"}], "has_more": False}
        )
    )
    assert [r["id"] for r in sync_client.subscriptions.iter_usage_records("sub_1")] == ["ur_1"]


@pytest.mark.asyncio
@respx.mock
async def test_usage_record_create_async(async_client: AsyncBillKit) -> None:
    respx.post("https://test.billkit.eu/v1/subscriptions/sub_1/usage_records").mock(
        return_value=httpx.Response(
            201, json={"id": "ur_1", "object": "usage_record", "quantity": 7}
        )
    )
    record = await async_client.subscriptions.create_usage_record("sub_1", quantity=7)
    assert record["quantity"] == 7


@pytest.mark.asyncio
@respx.mock
async def test_usage_record_list_async(async_client: AsyncBillKit) -> None:
    respx.get("https://test.billkit.eu/v1/subscriptions/sub_1/usage_records").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [], "has_more": False})
    )
    page = await async_client.subscriptions.list_usage_records("sub_1")
    assert page["object"] == "list"


@respx.mock
def test_prices_create_carries_usage_type(sync_client: BillKit) -> None:
    """``usage_type="metered"`` reaches the create body; omitting it drops
    the key so the server defaults to licensed."""
    route = respx.post("https://test.billkit.eu/v1/prices").mock(
        return_value=httpx.Response(
            200, json={"id": "price_1", "object": "price", "usage_type": "metered"}
        )
    )
    sync_client.prices.create(
        product_id="prod_api",
        amount_cents=5,
        currency="EUR",
        interval="month",
        usage_type="metered",
    )
    assert last_request_body(route)["usage_type"] == "metered"


@respx.mock
def test_prices_create_omits_usage_type_when_none(sync_client: BillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/prices").mock(
        return_value=httpx.Response(200, json={"id": "price_1", "object": "price"})
    )
    sync_client.prices.create(
        product_id="prod_1", amount_cents=999, currency="EUR", interval="month"
    )
    assert "usage_type" not in last_request_body(route)


# ─── Price archival (POST /v1/prices/{id}) ────────────────────────


@respx.mock
def test_price_update_archives(sync_client: BillKit) -> None:
    """``update(active=False)`` is the archive, and it returns the row.

    The price stays readable, so callers read ``active`` off the
    response instead of re-fetching. It was a ``DELETE`` until the verb
    was corrected: nothing was ever deleted, and subscriptions renew
    against the price by id.
    """
    route = respx.post("https://test.billkit.eu/v1/prices/price_1").mock(
        return_value=httpx.Response(200, json={"id": "price_1", "object": "price", "active": False})
    )
    archived = sync_client.prices.update("price_1", active=False)
    assert archived["active"] is False
    request = route.calls.last.request
    assert request.method == "POST"
    assert json.loads(request.read()) == {"active": False}
    assert_idempotency_header(request)


@respx.mock
def test_price_update_honours_explicit_idempotency_key(sync_client: BillKit) -> None:
    route = respx.post("https://test.billkit.eu/v1/prices/price_1").mock(
        return_value=httpx.Response(200, json={"id": "price_1", "active": False})
    )
    sync_client.prices.update("price_1", active=False, idempotency_key="archive-1")
    assert route.calls.last.request.headers["Idempotency-Key"] == "archive-1"


@pytest.mark.asyncio
@respx.mock
async def test_price_update_async(async_client: AsyncBillKit) -> None:
    respx.post("https://test.billkit.eu/v1/prices/price_1").mock(
        return_value=httpx.Response(200, json={"id": "price_1", "active": False})
    )
    archived = await async_client.prices.update("price_1", active=False)
    assert archived["active"] is False


@respx.mock
def test_price_update_puts_a_price_back_on_sale(sync_client: BillKit) -> None:
    """``active`` moves both ways.

    It decides what new checkouts may buy and nothing else, so neither
    direction can change what a past charge was made under, which is what
    price immutability actually protects.
    """
    route = respx.post("https://test.billkit.eu/v1/prices/price_1").mock(
        return_value=httpx.Response(200, json={"id": "price_1", "object": "price", "active": True})
    )
    back = sync_client.prices.update("price_1", active=True)
    assert back["active"] is True
    assert json.loads(route.calls.last.request.read()) == {"active": True}


def test_delete_is_only_where_the_object_leaves(
    sync_client: BillKit, async_client: AsyncBillKit
) -> None:
    """The catalogue is retired through its update route.

    Each of those used to carry a ``delete()``. None of them deleted
    anything: every one of those rows stays readable afterwards, which
    is why they have to. Customers and webhook endpoints really do leave
    the API, so they keep the verb.
    """
    for client in (sync_client, async_client):
        for name in ("prices", "products", "coupons", "tax_rates"):
            resource = getattr(client, name)
            assert not hasattr(resource, "delete"), f"{name}.delete should not exist"
            assert hasattr(resource, "update")
        assert hasattr(client.customers, "delete")
        # Configuration, not a record of money: a mistyped URL is removed.
        # Disabling stays beside it as the reversible act.
        assert hasattr(client.webhook_endpoints, "delete")
        assert hasattr(client.webhook_endpoints, "update")


@respx.mock
def test_delete_webhook_endpoint_sends_delete(sync_client: BillKit) -> None:
    route = respx.delete("https://test.billkit.eu/v1/webhook_endpoints/we_1").mock(
        return_value=httpx.Response(
            200, json={"id": "we_1", "object": "webhook_endpoint", "deleted": True}
        )
    )
    gone = sync_client.webhook_endpoints.delete("we_1", idempotency_key="drop-1")
    assert gone["deleted"] is True
    assert route.calls.last.request.headers["idempotency-key"] == "drop-1"


# ─── Subscription list filters ────────────────────────────────────


@respx.mock
def test_subscription_list_sends_renewal_state(sync_client: BillKit) -> None:
    """``renewal_state=paused`` is the only way to find paused rows.

    Pausing sets ``renewal_state`` and leaves ``status`` at ``active``,
    and the API rejects ``status=paused`` outright.
    """
    route = respx.get("https://test.billkit.eu/v1/subscriptions").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [], "has_more": False})
    )
    sync_client.subscriptions.list(renewal_state="paused")
    params = route.calls.last.request.url.params
    assert params["renewal_state"] == "paused"
    assert "status" not in params


@respx.mock
def test_subscription_list_sends_customer_and_csv_status(sync_client: BillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/subscriptions").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [], "has_more": False})
    )
    sync_client.subscriptions.list(customer_id="cus_1", status="active,past_due", limit=25)
    params = route.calls.last.request.url.params
    assert params["customer_id"] == "cus_1"
    assert params["status"] == "active,past_due"
    assert params["limit"] == "25"


@respx.mock
def test_subscription_iter_carries_filter_on_every_page(sync_client: BillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/subscriptions").mock(
        side_effect=[
            httpx.Response(
                200, json={"object": "list", "data": [{"id": "sub_1"}], "has_more": True}
            ),
            httpx.Response(
                200, json={"object": "list", "data": [{"id": "sub_2"}], "has_more": False}
            ),
        ]
    )
    walked = [s["id"] for s in sync_client.subscriptions.iter(renewal_state="paused", page_size=1)]
    assert walked == ["sub_1", "sub_2"]
    assert all(c.request.url.params["renewal_state"] == "paused" for c in route.calls)
    # Page 2 carries the cursor alongside the filter.
    assert route.calls[1].request.url.params["starting_after"] == "sub_1"


@pytest.mark.asyncio
@respx.mock
async def test_subscription_list_renewal_state_async(async_client: AsyncBillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/subscriptions").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [], "has_more": False})
    )
    await async_client.subscriptions.list(renewal_state="paused,canceling")
    assert route.calls.last.request.url.params["renewal_state"] == "paused,canceling"
