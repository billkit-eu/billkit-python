"""Python SDK integration suite, run against a **live** BillKit API.

Skipped unless ``BILLKIT_INTEGRATION_BASE_URL`` is set; boot a stack with
``make sdk-integration`` (see ``sdk/integration/SCENARIOS.md``).

Every test is tagged with a scenario id from
``sdk/integration/scenarios.json`` via the ``scenario`` marker, and
``test_zz_manifest_coverage`` asserts this suite covers **all** of them.
That assertion is what makes the parity matrix real: adding a scenario to
the manifest fails this suite until python implements it, and the node /
php suites carry the identical check.

The unit suites (``tests/test_*.py``) already cover transport, retry, and
error-mapping mechanics against a mock httpx. This suite deliberately does
*not* re-test those in isolation. It proves the SDK drives the real wire
contract: real cursor pagination, real idempotency records, and the real
money path through the fake Mollie provider.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from billkit import (
    AuthenticationError,
    BillKit,
    ConflictError,
    InvalidRequestError,
    PermissionError,
    ResourceMissingError,
    ServerError,
    WebhookSignature,
    WebhookVerificationError,
)
from tests.integration.conftest import (
    BASE_URL,
    ITTenant,
    MollieControl,
    deliver_mollie_webhook,
    idem_key,
    mint_scoped_key,
    provision_tenant,
)

pytestmark = pytest.mark.skipif(
    not BASE_URL,
    reason="Set BILLKIT_INTEGRATION_BASE_URL to run the SDK integration suite.",
)

MANIFEST = Path(__file__).resolve().parents[2] / "integration" / "scenarios.json"

#: Only scenarios in this family are required of the python SDK.
FAMILY = "server"

#: Scenario ids this module claims. Kept as an explicit set (rather than
#: harvested from marks at runtime) so the coverage test is independent of
#: pytest's collection order and of which subset was selected with ``-k``.
COVERED: set[str] = {
    "auth.valid_key",
    "auth.bad_key",
    "auth.scoped_key_denied",
    "crud.product",
    "crud.price",
    "crud.price_archive",
    "crud.customer",
    "crud.coupon",
    "crud.tax_rate",
    "crud.webhook_endpoint",
    "crud.price_decimal_rate",
    "crud.price_tiered",
    "crud.credit_note_absent_until_refunded",
    "filters.subscription_renewal_state",
    "filters.customer_provisional",
    "pagination.has_more",
    "pagination.auto_iter",
    "idempotency.replay",
    "idempotency.key_reuse_conflict",
    "idempotency.in_progress_converges",
    "errors.not_found",
    "errors.invalid_request",
    "errors.status_drives_class",
    "money.checkout_to_active",
    "money.partial_refund",
    "money.over_refund_rejected",
    "money.dispute_opened",
    "money.credit_note_for_refund",
    "money.void_refused_on_paid_invoice",
    "usage.record_and_replay",
    "usage.list_reconciliation",
    "usage.non_metered_rejected",
    "usage.dedupe_identifier",
    "usage.summary_forecast",
    "webhooks.verify_roundtrip",
    "webhooks.reject_tampered",
    "webhooks.reject_stale",
}


# ── shared helpers ───────────────────────────────────────────────────


def make_plan(
    c: BillKit,
    *,
    amount_cents: int = 2500,
    interval: str = "month",
    usage_type: str | None = None,
) -> dict[str, Any]:
    """A product + price pair the money specs charge against."""
    product = c.products.create(name=f"Plan {idem_key()}")
    price = c.prices.create(
        product_id=product["id"],
        amount_cents=amount_cents,
        currency="EUR",
        interval=interval,
        usage_type=usage_type,
    )
    return {"product": product, "price": price}


def checkout_to_active(c: BillKit, t: ITTenant, price_id: str) -> dict[str, Any]:
    """Take a checkout session all the way to an active subscription.

    Order matters and mirrors production: settle the payment at the provider
    *first*, then deliver the webhook. The API re-fetches payment state from
    the provider rather than trusting the webhook body, so a webhook
    delivered before the settle would correctly observe ``open`` and do
    nothing.
    """
    session = c.checkout_sessions.create(
        price_id=price_id,
        customer_email=f"buyer-{idem_key()}@sdk-it.example.com",
        success_url="https://merchant.example.com/ok",
        cancel_url="https://merchant.example.com/cancel",
    )
    provider_payment_id = MollieControl.payment_id_from_checkout_url(session["url"])
    MollieControl.settle(provider_payment_id, "paid")
    deliver_mollie_webhook(t.mollie_route_id, provider_payment_id)
    return {"session": session, "provider_payment_id": provider_payment_id}


def find_subscription(c: BillKit, price_id: str) -> dict[str, Any]:
    subs = c.subscriptions.list(limit=100)
    for row in subs["data"]:
        if row["price_id"] == price_id:
            return dict(row)
    raise AssertionError(f"no subscription found for price {price_id}")


def find_payment(c: BillKit, subscription_id: str) -> dict[str, Any]:
    payments = c.payments.list(limit=100)
    for row in payments["data"]:
        if row["subscription_id"] == subscription_id:
            return dict(row)
    raise AssertionError(f"no payment found for subscription {subscription_id}")


# ── auth ─────────────────────────────────────────────────────────────


def test_auth_valid_key(client: BillKit) -> None:
    """[auth.valid_key] a provisioned key reaches a real resource."""
    page = client.products.list()
    assert page["object"] == "list"
    assert isinstance(page["data"], list)


def test_auth_bad_key() -> None:
    """[auth.bad_key] an unknown key raises AuthenticationError."""
    bogus = BillKit(api_key="bk_test_0000000000000000000000", base_url=BASE_URL)
    with pytest.raises(AuthenticationError):
        bogus.products.list()


def test_auth_scoped_key_denied(tenant: ITTenant) -> None:
    """[auth.scoped_key_denied] a narrowly-scoped key is refused off-scope."""
    secret = mint_scoped_key(tenant, ["products:read"])
    scoped = BillKit(api_key=secret, base_url=BASE_URL)
    assert scoped.products.list()["object"] == "list"
    with pytest.raises(PermissionError):
        scoped.customers.list()


# ── crud ─────────────────────────────────────────────────────────────


def test_crud_product(client: BillKit) -> None:
    """[crud.product] product round-trips."""
    created = client.products.create(
        name="Round Trip", description="created by the python integration suite"
    )
    assert created["id"].startswith("prod_")

    assert client.products.retrieve(created["id"])["name"] == "Round Trip"
    assert client.products.update(created["id"], name="Round Trip v2")["name"] == "Round Trip v2"
    # Archive is the update route: the product has no delete, because an
    # archived product stays readable.
    assert client.products.update(created["id"], active=False)["active"] is False


def test_crud_price(client: BillKit) -> None:
    """[crud.price] price creates under a product and filters by product_id."""
    plan = make_plan(client, amount_cents=1234)
    price, product = plan["price"], plan["product"]
    assert price["amount_cents"] == 1234
    assert client.prices.retrieve(price["id"])["product_id"] == product["id"]

    filtered = client.prices.list(product_id=product["id"])
    assert price["id"] in [p["id"] for p in filtered["data"]]


def test_crud_price_archive(client: BillKit) -> None:
    """[crud.price_archive] archiving a price is readable and repeatable."""
    plan = make_plan(client, amount_cents=777)
    price, product = plan["price"], plan["product"]

    archived = client.prices.update(price["id"], active=False)
    assert archived["id"] == price["id"]
    assert archived["active"] is False

    # Archiving is not a delete: the row survives, so a subscription still
    # pointing at it can be read back rather than dangling.
    assert client.prices.retrieve(price["id"])["active"] is False
    listed = client.prices.list(product_id=product["id"])
    assert price["id"] in [p["id"] for p in listed["data"]]

    # Re-archiving returns it unchanged instead of erroring, which is what
    # makes a retried archive safe.
    again = client.prices.update(price["id"], active=False)
    assert again["id"] == price["id"]
    assert again["active"] is False

    # ``active`` moves both ways, and the money-bearing fields survive the
    # round trip, which is the immutability claim that actually matters.
    back = client.prices.update(price["id"], active=True)
    assert back["active"] is True
    assert back["amount_cents"] == 777


def test_crud_customer(client: BillKit) -> None:
    """[crud.customer] customer round-trips; delete removes it from the list."""
    email = f"cust-{idem_key()}@sdk-it.example.com"
    created = client.customers.create(email=email, name="Ada Lovelace")
    assert created["email"] == email
    assert client.customers.update(created["id"], name="Ada L.")["name"] == "Ada L."

    deleted = client.customers.delete(created["id"])
    # The customer leaves the API, so the body is a marker, not a row.
    assert deleted == {"id": created["id"], "object": "customer", "deleted": True}

    page = client.customers.list(limit=100)
    assert created["id"] not in [c["id"] for c in page["data"]]


def test_crud_coupon(client: BillKit) -> None:
    """[crud.coupon] coupon creates, validates, updates, withdraws."""
    code = f"SAVE{str(int(time.time()))[-8:]}"
    created = client.coupons.create(
        code=code, discount_type="percent", discount_value=25, duration="once"
    )
    assert created["code"] == code
    assert client.coupons.validate(code=code)["valid"] is True

    client.coupons.update(created["id"], max_redemptions=5)
    client.coupons.update(created["id"], active=False)

    # A withdrawn coupon must stop validating, otherwise a retired discount
    # would keep applying at checkout...
    assert client.coupons.validate(code=code)["valid"] is False
    # ...while staying readable, because a discount already applied to a
    # live subscription has to be traceable to the coupon behind it.
    assert client.coupons.retrieve(created["id"])["active"] is False


def test_crud_tax_rate(client: BillKit) -> None:
    """[crud.tax_rate] tax rate round-trips."""
    created = client.tax_rates.create(
        country_code="NL", rate_basis_points=2100, display_name="NL VAT"
    )
    assert created["rate_basis_points"] == 2100
    assert client.tax_rates.update(created["id"], rate_basis_points=900)["rate_basis_points"] == 900

    # Retiring is an update, and the rate stays readable: an invoice records
    # the percentage it charged, not the rate row.
    assert client.tax_rates.update(created["id"], active=False)["active"] is False
    assert client.tax_rates.retrieve(created["id"])["active"] is False


def test_crud_webhook_endpoint(client: BillKit) -> None:
    """[crud.webhook_endpoint] endpoint round-trips and rotates its secret."""
    created = client.webhook_endpoints.create(
        url="https://merchant.example.com/hooks/billkit",
        enabled_events=["*"],
        description="python integration suite",
    )
    # The signing secret is returned exactly once, on create.
    assert created["secret"].startswith("bkwhsec_")

    client.webhook_endpoints.update(created["id"], description="renamed")

    rotated = client.webhook_endpoints.rotate_secret(created["id"])
    assert rotated["secret"].startswith("bkwhsec_")
    assert rotated["secret"] != created["secret"]

    # Disabling stops delivery and keeps everything else, so the endpoint
    # is still listed and can be turned back on.
    disabled = client.webhook_endpoints.update(created["id"], status="disabled")
    assert disabled["status"] == "disabled"
    page = client.webhook_endpoints.list(limit=100)
    assert created["id"] in [e["id"] for e in page["data"]]

    # Deleting is the other act, and it is a real one: a URL registered by
    # mistake leaves the account rather than sitting there disabled for good.
    gone = client.webhook_endpoints.delete(created["id"])
    assert gone == {"id": created["id"], "object": "webhook_endpoint", "deleted": True}
    with pytest.raises(ResourceMissingError):
        client.webhook_endpoints.retrieve(created["id"])
    after = client.webhook_endpoints.list(limit=100)
    assert created["id"] not in [e["id"] for e in after["data"]]


def test_crud_price_decimal_rate(client: BillKit) -> None:
    """[crud.price_decimal_rate] a 12-dp rate round-trips byte-identical, as a string.

    The one scenario that can silently corrupt money: a ``float`` anywhere on
    the path rounds a per-call rate away, and the resulting invoice is wrong
    by orders of magnitude rather than by a cent.
    """
    product = client.products.create(name=f"Metered {idem_key()}")
    rate = "0.000000000001"  # twelve decimal places, in MINOR units
    price = client.prices.create(
        product_id=product["id"],
        currency="EUR",
        interval="month",
        usage_type="metered",
        unit_amount_decimal=rate,
    )
    assert isinstance(price["unit_amount_decimal"], str)
    assert price["unit_amount_decimal"] == rate

    # The read path is a separate serializer, so assert it separately.
    fetched = client.prices.retrieve(price["id"])
    assert isinstance(fetched["unit_amount_decimal"], str)
    assert fetched["unit_amount_decimal"] == rate


def test_crud_price_tiered(client: BillKit) -> None:
    """[crud.price_tiered] a tiered metered price round-trips every band."""
    product = client.products.create(name=f"Tiered {idem_key()}")
    price = client.prices.create(
        product_id=product["id"],
        currency="EUR",
        interval="month",
        usage_type="metered",
        billing_scheme="tiered",
        # Never defaulted: the same table under the two modes is a different
        # bill, not a rounding difference.
        tiers_mode="graduated",
        tiers=[
            {"up_to": 1000, "unit_amount_decimal": "0.05"},
            {"up_to": "inf", "unit_amount_decimal": "0.0125"},
        ],
    )
    assert price["billing_scheme"] == "tiered"
    assert price["tiers_mode"] == "graduated"
    assert len(price["tiers"]) == 2
    assert all(isinstance(tier["unit_amount_decimal"], str) for tier in price["tiers"])
    assert price["tiers"][0]["unit_amount_decimal"] == "0.05"
    assert price["tiers"][1]["unit_amount_decimal"] == "0.0125"


def test_crud_credit_note_absent_until_refunded(client: BillKit) -> None:
    """[crud.credit_note_absent_until_refunded] credit notes are issued, never created."""
    page = client.credit_notes.list(limit=10)
    assert page["object"] == "list"
    with pytest.raises(ResourceMissingError):
        client.credit_notes.retrieve("cn_does_not_exist")


# ── filters ──────────────────────────────────────────────────────────


def test_filters_subscription_renewal_state() -> None:
    """[filters.subscription_renewal_state] paused lives in renewal_state."""
    t = provision_tenant("renewal")
    c = BillKit(api_key=t.api_key, base_url=BASE_URL)
    price = make_plan(c, amount_cents=1500)["price"]
    checkout_to_active(c, t, price["id"])
    sub = find_subscription(c, price["id"])

    paused = c.subscriptions.pause(sub["id"])
    # The whole point: pausing lands in renewal_state and leaves status
    # alone, because the customer has paid for the period they are in.
    assert paused["renewal_state"] == "paused"
    assert paused["status"] == "active"

    by_renewal_state = c.subscriptions.list(renewal_state="paused")
    assert sub["id"] in [s["id"] for s in by_renewal_state["data"]]

    # ...and it is still an `active` subscription to the status filter.
    by_status = c.subscriptions.list(status="active")
    assert sub["id"] in [s["id"] for s in by_status["data"]]

    # `status=paused` is not a value the API accepts. It used to be, and
    # returned a confident, wrong, empty page; now it is refused so the
    # mistake is visible.
    with pytest.raises(InvalidRequestError) as excinfo:
        c.subscriptions.list(status="paused")
    assert excinfo.value.param == "status"


def test_filters_customer_provisional() -> None:
    """[filters.customer_provisional] buyers and abandoned carts are separable.

    A checkout that captures an email commits its Customer *before* the
    charge, so a checkout nobody finished leaves a row behind.
    ``provisional`` is the only thing that tells the two apart, and a
    fresh tenant is what makes the assertion exact.
    """
    t = provision_tenant("provisional")
    c = BillKit(api_key=t.api_key, base_url=BASE_URL)
    created = c.customers.create(email=f"buyer-{idem_key()}@example.com")

    def ids(**filters: Any) -> list[str]:
        return [row["id"] for row in c.customers.list(**filters)["data"]]

    assert created["id"] in ids(provisional=False)
    assert created["id"] not in ids(provisional=True)
    # Omitted means both kinds, which is why the filter has to be
    # reachable at all: the default answer is not the one a "list my
    # customers" screen wants.
    assert created["id"] in ids()


# ── pagination ───────────────────────────────────────────────────────


def test_pagination_has_more() -> None:
    """[pagination.has_more] a short limit reports has_more."""
    t = provision_tenant("page")
    c = BillKit(api_key=t.api_key, base_url=BASE_URL)
    for i in range(5):
        c.products.create(name=f"Paged {i}")

    page = c.products.list(limit=2)
    assert len(page["data"]) == 2
    assert page["has_more"] is True


def test_pagination_auto_iter() -> None:
    """[pagination.auto_iter] the iterator yields every row exactly once."""
    # A dedicated tenant so the expected set is exactly what we created.
    t = provision_tenant("iter")
    c = BillKit(api_key=t.api_key, base_url=BASE_URL)
    expected = {c.products.create(name=f"Iter {i}")["id"] for i in range(7)}

    seen = [p["id"] for p in c.products.iter(page_size=2)]
    # Exactly-once is the real assertion: a cursor that mis-orders ties shows
    # up here as a duplicate or a dropped row, not as a crash.
    assert len(seen) == len(expected)
    assert set(seen) == expected


# ── idempotency ──────────────────────────────────────────────────────


def test_idempotency_replay(client: BillKit) -> None:
    """[idempotency.replay] the same key + body replays the same resource."""
    key = idem_key()
    first = client.products.create(name="Idempotent Product", idempotency_key=key)
    second = client.products.create(name="Idempotent Product", idempotency_key=key)
    assert second["id"] == first["id"]


def test_idempotency_key_reuse_conflict(client: BillKit) -> None:
    """[idempotency.key_reuse_conflict] same key + different body conflicts."""
    key = idem_key()
    client.products.create(name="First Body", idempotency_key=key)
    with pytest.raises(ConflictError):
        client.products.create(name="Different Body", idempotency_key=key)


def test_idempotency_in_progress_converges() -> None:
    """[idempotency.in_progress_converges] one key, one resource, no raise.

    The contract a caller depends on: firing the same keyed create from N
    workers yields ONE resource and no exception.

    A request that arrives while the winner's handler is still running
    gets ``409 idempotency_in_progress`` — the one 4xx the client retries,
    because the charge may already have happened and the obvious
    workaround (retry with a fresh key) is what turns one charge into two.
    Whether any given attempt lands inside that window depends on the
    server's timing, so this can pass without entering it; what it can
    never do is pass while the client treats that 409 as terminal. The
    deterministic proof is in ``tests/test_retry.py``.
    """
    t = provision_tenant("inflight")
    c = BillKit(api_key=t.api_key, base_url=BASE_URL)
    key = idem_key()
    name = f"Concurrent {key}"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: c.products.create(name=name, idempotency_key=key),
                range(8),
            )
        )

    assert len({r["id"] for r in results}) == 1, "every attempt must resolve to the same product"

    # And the server really did create only one row.
    rows = [row for row in c.products.list(limit=100)["data"] if row["name"] == name]
    assert len(rows) == 1


# ── errors ───────────────────────────────────────────────────────────


def test_errors_not_found(client: BillKit) -> None:
    """[errors.not_found] 404 maps to ResourceMissingError."""
    with pytest.raises(ResourceMissingError):
        client.products.retrieve("prod_does_not_exist")


def test_errors_invalid_request(client: BillKit) -> None:
    """[errors.invalid_request] a validation failure carries ``param``.

    A 4-char currency fails schema validation. Asserting on ``param`` is
    the point: it proves the envelope's field-level detail survives the
    wire round-trip into the typed exception, which is what lets a caller
    highlight the offending input rather than show a generic error.
    """
    with pytest.raises(InvalidRequestError) as excinfo:
        client.prices.create(
            product_id="prod_whatever",
            amount_cents=100,
            currency="EURO",
            interval="month",
        )
    assert excinfo.value.param == "currency"
    assert excinfo.value.code == "parameter_invalid"


def test_errors_status_drives_class(tenant: ITTenant) -> None:
    """[errors.status_drives_class] a 4xx labelled ``api_error`` still maps on status.

    Not a contrived body: every request that never reaches a route handler
    is serialised by the API's framework-level handler as
    ``{"type": "api_error", "code": "unhandled"}`` with the original 4xx
    status. Mapping on ``type`` made a plain 404 — a typo'd id, an
    SDK/API version skew — arrive as ``ServerError``, which is the class
    retry and alerting policies key on.

    Driven through the transport rather than a resource because that is
    what a version skew looks like: the SDK asking for a route this API
    does not have.
    """
    c = BillKit(api_key=tenant.api_key, base_url=BASE_URL)
    with pytest.raises(ResourceMissingError) as excinfo:
        c._transport.request("GET", "/v1/no_such_resource")

    assert not isinstance(excinfo.value, ServerError)
    # The envelope value is still carried verbatim; it just does not
    # choose the class.
    assert excinfo.value.type == "api_error"
    assert excinfo.value.status_code == 404


# ── money ────────────────────────────────────────────────────────────


def test_money_checkout_to_active(client: BillKit, tenant: ITTenant) -> None:
    """[money.checkout_to_active] checkout settles into an active subscription."""
    price = make_plan(client, amount_cents=4200)["price"]
    checkout_to_active(client, tenant, price["id"])

    sub = find_subscription(client, price["id"])
    assert sub["status"] == "active"

    paid = find_payment(client, sub["id"])
    assert paid["status"] == "paid"
    assert paid["amount_cents"] == 4200


def test_money_partial_refund(client: BillKit, tenant: ITTenant) -> None:
    """[money.partial_refund] a partial refund leaves the remainder refundable."""
    price = make_plan(client, amount_cents=10_000)["price"]
    checkout_to_active(client, tenant, price["id"])
    sub = find_subscription(client, price["id"])
    payment = find_payment(client, sub["id"])

    refund = client.refunds.create(
        payment_id=payment["id"], amount_cents=3000, reason="integration partial"
    )
    assert refund["amount_cents"] == 3000

    after = client.payments.retrieve(payment["id"])
    assert after["amount_refunded_cents"] == 3000
    assert after["amount_refundable_cents"] == 7000


def test_money_over_refund_rejected(client: BillKit, tenant: ITTenant) -> None:
    """[money.over_refund_rejected] refunding beyond the balance is rejected.

    The guard that stops BillKit paying out more than it took.
    """
    price = make_plan(client, amount_cents=5000)["price"]
    checkout_to_active(client, tenant, price["id"])
    sub = find_subscription(client, price["id"])
    payment = find_payment(client, sub["id"])

    with pytest.raises(InvalidRequestError):
        client.refunds.create(payment_id=payment["id"], amount_cents=5001)


def test_money_dispute_opened(client: BillKit, tenant: ITTenant) -> None:
    """[money.dispute_opened] a chargeback opens a listable dispute."""
    price = make_plan(client, amount_cents=7700)["price"]
    result = checkout_to_active(client, tenant, price["id"])

    # Open the chargeback at the provider, then re-deliver the payment
    # webhook. The reconciler picks the transition up on that hop.
    MollieControl.chargeback(result["provider_payment_id"], "77.00", "fraudulent")
    deliver_mollie_webhook(tenant.mollie_route_id, result["provider_payment_id"])

    disputes = client.disputes.list(limit=100)
    match = [d for d in disputes["data"] if d["amount_cents"] == 7700]
    assert match, "a dispute should exist for the charged-back payment"
    assert match[0]["status"] == "open"
    assert client.disputes.retrieve(match[0]["id"])["id"] == match[0]["id"]


def test_money_credit_note_for_refund(client: BillKit, tenant: ITTenant) -> None:
    """[money.credit_note_for_refund] a settled refund issues a retrievable credit note."""
    price = make_plan(client, amount_cents=6400)["price"]
    result = checkout_to_active(client, tenant, price["id"])
    sub = find_subscription(client, price["id"])
    payment = find_payment(client, sub["id"])

    invoices = client.invoices.list(limit=100)
    invoice = next(i for i in invoices["data"] if i["payment_id"] == payment["id"])

    refund = client.refunds.create(payment_id=payment["id"], amount_cents=6400)
    # Nothing yet: the refund is pending and may still fail, and a gapless
    # series cannot un-issue a number.
    assert refund["status"] == "pending"
    assert client.credit_notes.list(invoice_id=invoice["id"])["data"] == []

    MollieControl.settle_refunds_for(result["provider_payment_id"], "refunded")
    deliver_mollie_webhook(tenant.mollie_route_id, result["provider_payment_id"])

    notes = client.credit_notes.list(invoice_id=invoice["id"])["data"]
    assert len(notes) == 1
    note = notes[0]
    assert note["invoice_id"] == invoice["id"]
    # Its own series, deliberately distinct from the invoice's: a tax
    # authority reads the two as different document classes.
    assert note["number"].startswith("CN-")
    assert note["number"] != invoice["number"]
    # The identity the whole document rests on.
    assert note["subtotal_cents"] + note["tax_cents"] == note["total_cents"] == 6400

    fetched = client.credit_notes.retrieve(note["id"])
    assert fetched["id"] == note["id"]
    assert fetched["object"] == "credit_note"


def test_money_void_refused_on_paid_invoice(client: BillKit, tenant: ITTenant) -> None:
    """[money.void_refused_on_paid_invoice] voiding a paid invoice is a typed conflict."""
    price = make_plan(client, amount_cents=1900)["price"]
    checkout_to_active(client, tenant, price["id"])
    sub = find_subscription(client, price["id"])

    invoices = client.invoices.list(limit=100)
    invoice = next(i for i in invoices["data"] if i["subscription_id"] == sub["id"])
    assert invoice["status"] == "paid"

    # Not a limitation — the contract. Voiding claims the sale was never
    # owed, which is false once the money moved; the reversal there is a
    # credit note.
    with pytest.raises(ConflictError) as excinfo:
        client.invoices.void(invoice["id"])
    assert excinfo.value.code == "invoice_not_voidable"


# ── usage ────────────────────────────────────────────────────────────


def active_subscription(c: BillKit, t: ITTenant, price_id: str) -> dict[str, Any]:
    """Mint an ACTIVE subscription on ``price_id`` and return it.

    Same machinery as the money specs: checkout -> settle at the fake
    Mollie -> deliver the webhook, then find the subscription by price.
    """
    checkout_to_active(c, t, price_id)
    sub = find_subscription(c, price_id)
    assert sub["status"] == "active"
    return sub


def test_usage_record_and_replay(client: BillKit, tenant: ITTenant) -> None:
    """[usage.record_and_replay] a usage record posts and replays by key.

    Replaying the same Idempotency-Key must return the same record, not
    double-count the usage: that is what makes at-least-once reporting
    pipelines safe to retry.
    """
    price = make_plan(client, amount_cents=5, usage_type="metered")["price"]
    sub = active_subscription(client, tenant, price["id"])

    key = idem_key()
    record = client.subscriptions.create_usage_record(
        sub["id"], quantity=42, metadata={"source": "python-it"}, idempotency_key=key
    )
    assert record["object"] == "usage_record"
    assert record["subscription_id"] == sub["id"]
    assert record["quantity"] == 42
    assert record["invoice_id"] is None

    replay = client.subscriptions.create_usage_record(
        sub["id"], quantity=42, metadata={"source": "python-it"}, idempotency_key=key
    )
    assert replay["id"] == record["id"]


def test_usage_list_reconciliation(client: BillKit, tenant: ITTenant) -> None:
    """[usage.list_reconciliation] invoice_id=pending returns the posted records."""
    price = make_plan(client, amount_cents=3, usage_type="metered")["price"]
    sub = active_subscription(client, tenant, price["id"])

    posted = [
        client.subscriptions.create_usage_record(sub["id"], quantity=q)["id"] for q in (10, 20, 30)
    ]

    pending = client.subscriptions.list_usage_records(sub["id"], invoice_id="pending", limit=100)
    assert pending["object"] == "list"
    ids = [r["id"] for r in pending["data"]]
    for record_id in posted:
        assert record_id in ids
    # Nothing pending may already claim an invoice.
    assert all(r["invoice_id"] is None for r in pending["data"])


def test_usage_non_metered_rejected(client: BillKit, tenant: ITTenant) -> None:
    """[usage.non_metered_rejected] posting usage to a licensed subscription is a typed 400."""
    price = make_plan(client, amount_cents=2500)["price"]
    sub = active_subscription(client, tenant, price["id"])

    with pytest.raises(InvalidRequestError):
        client.subscriptions.create_usage_record(sub["id"], quantity=1)


def test_usage_dedupe_identifier(client: BillKit, tenant: ITTenant) -> None:
    """[usage.dedupe_identifier] the same identifier under a different key dedupes."""
    price = make_plan(client, amount_cents=7, usage_type="metered")["price"]
    sub = active_subscription(client, tenant, price["id"])

    identifier = f"job-{idem_key()}"
    first = client.subscriptions.create_usage_record(
        sub["id"], quantity=9, identifier=identifier, idempotency_key=idem_key()
    )
    # A DIFFERENT idempotency key, so the transport-level replay guard cannot
    # be what dedupes this. Only the natural key can.
    second = client.subscriptions.create_usage_record(
        sub["id"], quantity=9, identifier=identifier, idempotency_key=idem_key()
    )
    assert second["id"] == first["id"]

    pending = client.subscriptions.list_usage_records(sub["id"], invoice_id="pending", limit=100)
    assert [r["id"] for r in pending["data"]].count(first["id"]) == 1


def test_usage_summary_forecast(client: BillKit, tenant: ITTenant) -> None:
    """[usage.summary_forecast] usage_summary says what the next close will bill."""
    price = make_plan(client, amount_cents=11, usage_type="metered")["price"]
    sub = active_subscription(client, tenant, price["id"])
    for quantity in (100, 250):
        client.subscriptions.create_usage_record(sub["id"], quantity=quantity)

    summary = client.subscriptions.retrieve_usage_summary(sub["id"])
    assert summary["subscription_id"] == sub["id"]
    assert summary["pending_quantity"] == 350
    assert summary["pending_record_count"] == 2
    # 350 units at 11 cents. The forecast and the close share one predicate
    # server-side, so this is the invoice, not an estimate.
    assert summary["net_cents"] == 3850
    assert summary["net_cents"] + summary["tax_cents"] == summary["gross_cents"]
    assert summary["will_charge"] is True


# ── webhooks ─────────────────────────────────────────────────────────

SECRET = "bkwhsec_integration_secret"
BODY = json.dumps({"id": "evt_1", "type": "subscription.created"})


def _sign(secret: str, body: str, ts: int) -> str:
    """Sign the documented wire format: ``"{t}." + rawBody``, HMAC-SHA256, hex."""
    mac = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def test_webhooks_verify_roundtrip() -> None:
    """[webhooks.verify_roundtrip] a correctly signed payload verifies."""
    event = WebhookSignature.verify(
        payload=BODY,
        signature_header=_sign(SECRET, BODY, int(time.time())),
        secret=SECRET,
    )
    assert event["id"] == "evt_1"


def test_webhooks_reject_tampered() -> None:
    """[webhooks.reject_tampered] a mutated body fails verification."""
    header = _sign(SECRET, BODY, int(time.time()))
    with pytest.raises(WebhookVerificationError):
        WebhookSignature.verify(
            payload=BODY.replace("evt_1", "evt_2"), signature_header=header, secret=SECRET
        )


def test_webhooks_reject_stale() -> None:
    """[webhooks.reject_stale] a timestamp outside tolerance fails verification."""
    stale = int(time.time()) - 10_000
    with pytest.raises(WebhookVerificationError):
        WebhookSignature.verify(
            payload=BODY, signature_header=_sign(SECRET, BODY, stale), secret=SECRET
        )


# ── parity gate ──────────────────────────────────────────────────────


def test_zz_manifest_coverage() -> None:
    """This suite must implement every scenario in the shared manifest.

    This is what turns the parity matrix from documentation into a build
    gate. Adding an entry to ``sdk/integration/scenarios.json`` fails
    *every* SDK suite that has not implemented it yet, so a capability
    can't land in one language and quietly skip the others. The node and
    php suites carry the identical check against the same file.
    """
    manifest = json.loads(MANIFEST.read_text())
    # Only this SDK's family is required of it: a server API client has no
    # iframe to mount, and a browser SDK has no `crud.product`.
    required = {s["id"] for s in manifest["scenarios"] if s["family"] == FAMILY}

    missing = sorted(required - COVERED)
    unknown = sorted(COVERED - required)

    assert not missing, (
        f"Not implemented by the python suite: {missing}. Implement them, or drop "
        "them from sdk/integration/scenarios.json if the capability is genuinely "
        "gone from every SDK."
    )
    assert not unknown, (
        f"Claimed ids that are not in the manifest: {unknown}. Add them to "
        "sdk/integration/scenarios.json so node + php are held to the same bar "
        "(that is the whole point of the manifest)."
    )
