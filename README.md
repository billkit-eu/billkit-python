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

client = BillKit(api_key="sk_test_...")

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
```

## Async

```python
from billkit import AsyncBillKit

async with AsyncBillKit(api_key="sk_test_...") as client:
    customer = await client.customers.create(email="ada@example.com")
```

## Configuration

```python
from billkit import BillKit, RetryPolicy

client = BillKit(
    api_key="sk_test_...",                  # or set BILLKIT_API_KEY
    base_url="https://api.billkit.eu",   # override for self-hosted
    timeout=30.0,                          # seconds, or pass httpx.Timeout
    retry_policy=RetryPolicy(
        max_attempts=5,
        max_retry_after_seconds=10.0,       # cap 429 Retry-After sleeps
    ),
)
```

The SDK auto-generates an `Idempotency-Key` for every mutating call, so 5xx and short `Retry-After` 429 retries are safe: the server replays the original response when an earlier attempt completed. Pass `idempotency_key=` to coalesce retries across process restarts.

## Errors

```python
from billkit import BillKit, ResourceMissingError, RateLimitError, BillKitError

client = BillKit(api_key="sk_test_...")
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
