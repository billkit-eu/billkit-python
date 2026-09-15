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
    "filters.subscription_renewal_state",
    "pagination.has_more",
    "pagination.auto_iter",
    "idempotency.replay",
    "idempotency.key_reuse_conflict",
    "errors.not_found",
    "errors.invalid_request",
    "money.checkout_to_active",
    "money.partial_refund",
    "money.over_refund_rejected",
    "money.dispute_opened",
    "usage.record_and_replay",
    "usage.list_reconciliation",
    "usage.non_metered_rejected",
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
    bogus = BillKit(api_key="sk_test_0000000000000000000000", base_url=BASE_URL)
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
    assert created["secret"].startswith("whsec_")

    client.webhook_endpoints.update(created["id"], description="renamed")

    rotated = client.webhook_endpoints.rotate_secret(created["id"])
    assert rotated["secret"].startswith("whsec_")
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


# ── webhooks ─────────────────────────────────────────────────────────

SECRET = "whsec_integration_secret"
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
