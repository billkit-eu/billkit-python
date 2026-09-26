# BillKit Python SDK

Async + sync client for [BillKit](https://billkit.eu), the Stripe-Billing-shape multi-tenant SaaS API on Mollie.

## Install

```bash
pip install billkit-eu
# or
uv add billkit-eu
```

Requires Python 3.11+. The distribution is `billkit-eu` because the bare
`billkit` name on PyPI belongs to an unrelated project. The import name is
unaffected:

```python
import billkit
```

## Quick start

```python
from billkit import BillKit

client = BillKit(api_key="bk_test_...")

customer = client.customers.create(email="ada@example.com", name="Ada Lovelace")
product = client.products.create(
    name="Pro",
    description="Hosted billing for SaaS",
    marketing_features=["Checkout", "Subscriptions"],
)
price = client.prices.create(
    product_id=product["id"],
    amount_cents=999,
    currency="EUR",
    interval="month",
    trial_days=14,
    payment_methods=["creditcard", "directdebit"],
)
session = client.checkout_sessions.create(
    customer_id=customer["id"],
    price_id=price["id"],
    success_url="https://app.example.com/success",
    cancel_url="https://app.example.com/cancel",
)
print(session["url"])  # redirect the user here
```

## One-shot (mandate-less) payments

A one-shot is a single charge with no subscription, mandate or renewals: the Stripe PaymentIntent shape, mapped onto Mollie. Create it, redirect to `redirect_url`, and settle terminal state via the `one_shot_payment.succeeded` / `.failed` webhook events.

```python
payment = client.one_shot_payments.create(
    customer_id=customer["id"],
    amount_cents=2500,
    currency="EUR",
    method="ideal",
    success_url="https://shop.example.com/thanks",
    refund_window_days=14,  # optional; 0 disables refunds, default is 30
)
print(payment["redirect_url"])  # redirect the payer here

# Later, refund it within its window (omit amount_cents for a full refund):
client.refunds.create(one_shot_payment_id=payment["id"])
# ...or refund part of it. A charge can carry several partials:
client.refunds.create(one_shot_payment_id=payment["id"], amount_cents=500)

# List one customer's one-off charges, newest first (payments.list only
# lists subscription payments):
for charge in client.one_shot_payments.iter(customer_id=customer["id"], status="paid"):
    print(charge["id"], charge["amount_cents"])
```

## Metered billing

A metered price charges for what was consumed. You report usage, and at each period close BillKit invoices the period's total and charges the stored mandate.

There are three ways to price a unit, and a price uses exactly one of them.

```python
# 1. Whole minor units: 5 cents per unit.
client.prices.create(
    product_id=product["id"], amount_cents=5,
    currency="EUR", interval="month", usage_type="metered",
)

# 2. Finer than a minor unit. "0.02" is 0.02 CENTS, i.e. EUR 0.0002 per unit
#    -- the canonical per-API-call price, which no integer can express.
client.prices.create(
    product_id=product["id"], unit_amount_decimal="0.02",
    currency="EUR", interval="month", usage_type="metered",
)

# 3. By bands. "graduated" prices the units inside each band; "volume" lets
#    the period total pick one band which then prices every unit. The same
#    table under the two modes is a different bill, so the mode is required.
client.prices.create(
    product_id=product["id"], currency="EUR", interval="month",
    usage_type="metered", billing_scheme="tiered", tiers_mode="graduated",
    tiers=[
        {"up_to": 1000, "unit_amount": 1},        # first 1,000 at EUR 0.01
        {"up_to": "inf", "unit_amount_decimal": "0.5"},  # then EUR 0.005
    ],
)
```

**`unit_amount_decimal` is a string, and a `float` is refused.** Pass `str`, `int` or `Decimal`; a float raises `TypeError`. A float cannot hold 0.0002 exactly, so accepting one would work for the values that happen to round-trip and silently mis-price the ones that do not. The same rule applies inside a tier.

### Reporting usage, exactly once

```python
client.subscriptions.create_usage_record(
    sub["id"],
    quantity=1200,
    identifier="job-2026-09-19T10:00Z",  # your id for what you are metering
)
```

Two dedupe mechanisms, for two different failures. The `Idempotency-Key` the SDK sends covers a retry of *that HTTP request*, including its own internal retries. `identifier` covers a retry of *your* call -- a job runner replaying a task, a queue delivering twice, your code re-invoking after its own timeout -- which reaches the API as a genuinely new request with a new key. A second report of the same identifier returns the first record unchanged rather than billing twice. If your reporting pipeline is at-least-once, `identifier` is the one that matters.

### Knowing what the next invoice will be

```python
summary = client.subscriptions.retrieve_usage_summary(sub["id"])
summary["pending_quantity"]  # 3
summary["gross_cents"]       # 15
summary["will_charge"]       # False
```

Check `will_charge` before you promise a customer an amount. A period whose total is under `minimum_charge_cents` (EUR 1.00) is **not** charged, because the payment provider would refuse it. The usage is not lost: it stays pending and rolls into the next period, which is then billed for both. `open_invoice_id` names an earlier cycle that is invoiced and still unsettled; while one is open, this period cannot be charged.

## Telling buyers from abandoned carts

A checkout that captures an email commits its Customer **before** the charge,
so a checkout nobody finished leaves a row behind. `provisional` is what
separates the two:

```python
paid = client.customers.list(provisional=False)   # people who bought
carts = client.customers.list(provisional=True)   # the cart-recovery worklist
everyone = client.customers.list()                # both kinds, the default
```

Abandoned rows are swept after the tenant's retention window.

## Finding paused subscriptions

`status` and `renewal_state` answer different questions, and only one of them knows about pausing. `status` is where the subscription stands with its payments (`incomplete`, `trialing`, `active`, `past_due`, `canceled`). `renewal_state` is what happens when the current period ends (`auto_renew`, `paused`, `canceling`, `stopped`). Pausing sets `renewal_state` and leaves `status` at `active`, because the customer has paid for the period they are in:

```python
paused = client.subscriptions.list(renewal_state="paused")

# Both filters take a comma-separated list, and carry onto every page:
for sub in client.subscriptions.iter(status="active,past_due", page_size=100):
    ...
```

`status="paused"` is not an accepted value and raises `InvalidRequestError`.

## Clearing a field

On an update, an explicit `None` clears a field and omitting the keyword leaves the stored value alone. This applies to `products.update` (`description`, `default_price_id`, `marketing_features` empties the list), `prices.update` (`refund_window_initial_days` and `refund_window_renewal_days` drop the price's override), `customers.update` (`name`), `webhook_endpoints.update` (`description`), `coupons.update` (`max_redemptions` removes the cap, `redeem_by` removes the expiry, `applies_to_price_ids` lifts the price restriction, `min_amount_cents` lifts the minimum) and `tax_rates.update` (`display_name`). Every other keyword still treats `None` as "not given" and leaves it out of the request, because the API refuses a null on a field it cannot clear with a 400 naming the field. A `metadata` update replaces the stored object whole, so `metadata={}` is how it is emptied.

```python
client.coupons.update(coupon.id, max_redemptions=None)  # remove the cap
client.customers.update(customer.id, email="new@example.com")  # name untouched
```

A `None` passed straight through from your own data therefore clears the field. When a value may be missing and you mean "leave it", omit the keyword.

## Retiring something, and deleting something

`delete()` exists on `customers` and `webhook_endpoints`, and it returns `{"id": ..., "object": ..., "deleted": True}` rather than the object: it has left the API, so there is nothing to hand back. A deleted endpoint takes its delivery rows with it, because those are readable only through the endpoint that owns them; the events stay in `client.events`, which is the record of what you were sent.

The catalogue is retired through its update route instead, because it stays readable afterwards. Prices, products, tax rates and coupons take `active=False`. Each of them has to survive: subscriptions renew against a price by id, an invoice records the VAT percentage a tax rate produced, and a redeemed coupon is part of what a customer was charged.

`status="disabled"` on a webhook endpoint is the other half of the pair, not a substitute for deleting. It stops delivery and keeps the endpoint, its secret and its history, and it can be turned back on.

A price accepts `active` and nothing else, because the amount, currency and interval are fixed at creation. `active` itself moves both ways: it decides what new checkouts may buy, not what anyone was charged. Re-sending the value it already has is a no-op, so a retry is safe.

```python
# Stop selling a price. It stays readable; customers on it keep renewing.
archived = client.prices.update(price["id"], active=False)
assert archived["active"] is False

# Stop sending to an endpoint, without losing its signing secret.
client.webhook_endpoints.update(endpoint["id"], status="disabled")

# Remove a customer. Refused while they hold a subscription that can
# still charge them.
client.customers.delete(customer["id"])  # -> {"deleted": True, ...}
```

## Invoice and credit-note PDFs

```python
from pathlib import Path

Path("invoice.pdf").write_bytes(client.invoices.retrieve_pdf("inv_123"))
Path("credit-note.pdf").write_bytes(client.credit_notes.retrieve_pdf("cn_123"))
```

Returns the raw bytes. S3-backed deployments answer with a redirect to a presigned URL, which the SDK follows under its own timeout and retry policy, so both storage adapters look the same from here — and the API key is never sent to the storage host, because the presigned URL carries its own credential. A deployment with PDF rendering disabled answers `501`, which surfaces as a `ServerError` with `code == "rendering_pending"`; `retrieve()` still gives you the structured document to render yourself.

## Async

```python
from billkit import AsyncBillKit

async with AsyncBillKit(api_key="bk_test_...") as client:
    customer = await client.customers.create(email="ada@example.com")
```

## Configuration

```python
from billkit import BillKit, RetryPolicy

client = BillKit(
    api_key="bk_test_...",  # or set BILLKIT_API_KEY
    base_url="https://api.billkit.eu",  # override for self-hosted
    timeout=30.0,  # seconds, or pass httpx.Timeout
    retry_policy=RetryPolicy(
        max_attempts=5,
        max_retry_after_seconds=10.0,  # cap 429 Retry-After sleeps
    ),
)
```

The SDK auto-generates an `Idempotency-Key` for every mutating call, so 5xx and short `Retry-After` 429 retries are safe: the server replays the original response when an earlier attempt completed. Pass `idempotency_key=` to coalesce retries across process restarts.

`409 idempotency_in_progress` is retried too. It means an earlier request carrying the same key is still in flight, which is the one 4xx where giving up is the dangerous answer: that request may already have charged the customer, and the obvious workaround — retry with a *fresh* key — is exactly what turns one charge into two. The retry reuses the original key, so it either loses the race again or replays the first call's result. Every other 409 (`idempotency_key_in_use`, a conflicting subscription state) fails immediately, because retrying can only repeat it.

## Errors

```python
from billkit import BillKit, ResourceMissingError, RateLimitError, BillKitError

client = BillKit(api_key="bk_test_...")
try:
    customer = client.customers.retrieve("cus_doesnt_exist")
except ResourceMissingError:
    print("Customer is gone")
except RateLimitError as exc:
    print(f"Rate limited; retry in {exc.retry_after}s")
except BillKitError as exc:
    print(f"BillKit error {exc.status_code}: {exc.message}")
```

All errors inherit from `BillKitError`. Subclasses: `APIConnectionError`, `APIError`, `ServerError`, `AuthenticationError`, `PermissionError`, `ResourceMissingError`, `InvalidRequestError`, `ConflictError`, `RateLimitError`.

The class is chosen by **HTTP status**, not by the envelope's `type`:

| Status | Class |
|---|---|
| 401 | `AuthenticationError` |
| 403 | `PermissionError` |
| 404 | `ResourceMissingError` |
| 409 | `ConflictError` |
| 429 | `RateLimitError` |
| other 4xx (400, 405, 422, …) | `InvalidRequestError` |
| 5xx | `ServerError` |

The status is the field the API cannot get wrong. Requests that never reach a route handler — an unmatched path, a method the route does not allow — are serialised by the framework as `{"type": "api_error", "code": "unhandled"}` *with a 4xx status*, so mapping on `type` would turn a plain 404 into a `ServerError` and tell you BillKit had broken when the request was at fault. The envelope's `type`, `code` and `param` are all still on the raised object if you want them.

## Logging

The SDK is **silent by default**. It owns one logger, `logging.getLogger("billkit")`, with a `NullHandler` attached, and it never calls `basicConfig`, never sets a level, and never adds a handler to a logger it doesn't own. Your logging config is yours.

Turn it on from your application:

```python
import logging

logging.basicConfig()
logging.getLogger("billkit").setLevel(logging.DEBUG)
```

```
DEBUG:billkit:BillKit request POST https://api.billkit.eu/v1/customers (attempt 1/3)
DEBUG:billkit:BillKit response POST https://api.billkit.eu/v1/customers -> 503 in 84ms (request_id=req_9f2a)
WARNING:billkit:BillKit retrying POST https://api.billkit.eu/v1/customers after HTTP 503 (attempt 1) in 500ms
DEBUG:billkit:BillKit response POST https://api.billkit.eu/v1/customers -> 200 in 91ms (request_id=req_9f2b)
```

- **DEBUG**: one line per attempt, one per response (status, elapsed ms, `X-Request-Id`; quote that id to support).
- **WARNING**: one line per retry, with the reason and the delay before the next attempt.

**Never logged:** your API key or the `Authorization` header; request and response **bodies** (they carry customer PII); the **query string** (list filters carry values like `email=`); only the path is logged. The final failure isn't logged either: it's raised as a typed `BillKitError` carrying the status, request id and retry-after, and logging it here too would hand you a duplicate you can't suppress.

### One caveat: httpx's own request line

The promise above covers records **this SDK** writes. `httpx`, the HTTP client underneath, writes its own at `INFO`, and it includes the full URL:

```
INFO:httpx:HTTP Request: GET https://api.billkit.eu/v1/customers?email=ada@example.com "HTTP/1.1 200 OK"
```

`basicConfig()` plus a `DEBUG` level on `billkit` is enough to surface it, so turning BillKit's logging on would otherwise put customer emails in your logs from a logger BillKit never touched. There is no per-client switch for it in httpx.

So when you opt this SDK in, it raises the `httpx` and `httpcore` loggers to `WARNING` — **only** if you have not set a level on them yourself, and **only** for the `httpx` client the SDK created. Both exceptions are deliberate:

- If you have configured `httpx` logging, you made a decision and a billing SDK does not get to overrule it. Silence the request line yourself, or accept the query strings.
- If you passed your own `httpx_client=`, you own its logging as much as its connection pooling.

The check runs when the client is constructed and again on its first request, so either startup order works: configure your logging before you build the client, or after.

The logger object is exported if you'd rather wire it up directly:

```python
from billkit import logger

logger.addHandler(my_handler)
```

## Webhook verification

```python
from billkit import WebhookSignature, WebhookVerificationError

# In your FastAPI / Flask / Django handler:
try:
    event = WebhookSignature.verify(
        payload=request.body,
        signature_header=request.headers.get("BillKit-Signature"),
        secret=os.environ["BILLKIT_WEBHOOK_SECRET"],
    )
except WebhookVerificationError:
    return Response(status_code=400)

if event["type"] == "subscription.created":
    handle_new_subscription(event["data"])
```

The verifier enforces a 5-minute timestamp tolerance (replay protection) and constant-time HMAC compare. Pass `tolerance_seconds=` to customise.

## Development

```bash
uv sync --all-extras --dev
uv run pytest
uv run ruff check
uv run mypy src
```

## License

Proprietary.
