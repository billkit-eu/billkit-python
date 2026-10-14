# BillKit Python SDK

Async + sync client for [BillKit](https://billkit.eu), the Stripe-Billing-shape multi-tenant SaaS API on Mollie.

## Install

```bash
pip install billkit-eu
# or
uv add billkit-eu
```

Requires Python 3.11+, `httpx` (0.28 or later, below 1.0), `pydantic` 2.7+ and `anyio` (which httpx already installs). The
distribution is `billkit-eu` because the bare `billkit` name on PyPI belongs to
an unrelated project. The import name is unaffected:

```python
import billkit
```

The package follows [semantic versioning](https://semver.org) from 1.0.0. Coming
from 0.x? [CHANGELOG.md](CHANGELOG.md#upgrading-from-0x) lists every change
that needs an edit, with the code before and after.

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
    product_id=product.id,
    amount_cents=999,
    currency="EUR",
    interval="month",
    trial_days=14,
    payment_methods=["creditcard", "directdebit"],
)
session = client.checkout_sessions.create(
    customer_id=customer.id,
    price_id=price.id,
    success_url="https://app.example.com/success",
    cancel_url="https://app.example.com/cancel",
)
print(session.url)  # redirect the user here
```

## Responses

Every method returns a typed object from `billkit.types` (`Customer`,
`Subscription`, `Invoice`, ...), generated from the API's public OpenAPI
document. Read fields as attributes, with your editor and type checker knowing
each type, or as keys, the way 0.x dicts were read. Both styles are permanent;
neither is deprecated and neither warns.

```python
sub = client.subscriptions.retrieve("sub_123")

sub.status                    # "active", typed as str
sub["status"]                 # the same value
sub.get("discount")           # None: the subscription has no discount
"discount" in sub             # True: the API sent the key (as null)
sub.customer                  # None unless you asked for expand=["customer"]
sub.to_dict()                 # plain JSON types, recursively, exactly the keys sent
```

**Nothing the API adds can break a call.** Responses are not validated. A
field this SDK version has never heard of is kept, readable as `obj["new_field"]`
(the spelling a type checker accepts; `obj.new_field` also works at runtime)
and included by `to_dict()`. An enum-like field is a
plain `str`, so a value added later comes through as itself; the values this
version knows are listed for comparison only:

```python
from billkit.types import RenewalState, SubscriptionStatus

if sub.status == SubscriptionStatus.PAST_DUE:
    ...
elif sub.renewal_state == RenewalState.PAUSED:
    ...
# Anything else is a state added after this release: handle it as "unknown".
```

An object is a read-only `Mapping` (`dict(obj)`, `{**obj}`, iterating its
keys), not a `dict`: check `isinstance(x, collections.abc.Mapping)`, and call
`to_dict()` before `json.dumps`, before mutating, or to compare with a literal.
`model_dump()`, `model_dump_json()` and FastAPI's `jsonable_encoder` return
the same thing as `to_dict()` (Pydantic's `include`/`exclude` options do not
apply; filter the dict). Two objects are equal when they are the same class
and their `to_dict()` is equal, so which keys were sent counts; an object
never equals a `dict`. Attributes are writable, as in other Python payment
SDKs, but the mapping view is read-only. Objects and pages pickle.

A declared field the API did not send reads as `None` as an attribute and is
absent as a key, which is exactly how the 0.x dicts behaved.

A field whose wire name would shadow a model method (`items`, `keys`,
`values`, `get`, `to_dict`, `copy`, `dict`, `json`, `model_*`) or is a Python
keyword is declared with a trailing underscore: `obj.items_` as an attribute,
`obj["items"]` as a key, `"items"` in `to_dict()`.

## Pagination

`list()` returns one page, a `ListObject`. Iterating it yields the items,
`len(page)` counts them and `item in page` looks for one. A page is not a
`Mapping`; the envelope is still readable with a string key (`page["data"]`,
`page["has_more"]`, `page.get("object")`, `"has_more" in page`), and any
other key is a `TypeError`. `page.to_dict()` is the whole envelope as JSON.

```python
page = client.customers.list(limit=50)
for customer in page:
    print(customer.id)
if page.has_more:
    next_page = client.customers.list(limit=50, starting_after=page.data[-1].id)
```

`iter()` walks every page for you, forward-only. It takes every filter `list()`
takes and carries them onto every page, with `limit` as the page size and a `starting_after`
to resume from:

```python
for customer in client.customers.iter(provisional=False, expand=["stats"], limit=100):
    print(customer.id, customer.stats)
```

## Per-request options

Every method accepts these keyword-only options after its own arguments. They
apply to that call only.

| Option | Meaning |
|---|---|
| `idempotency_key` | Mutating methods only. Your own `Idempotency-Key`; see **Idempotency** below. |
| `timeout` | Seconds, or an `httpx.Timeout`. `None` disables the timeout. Overrides the client's. |
| `max_retries` | Retries after the first attempt; `0` sends exactly one. Overrides the client's `RetryPolicy`. Which failures are retried does not change. |
| `extra_headers` | A mapping of headers added to this call, matched case-insensitively. `Authorization`, `Idempotency-Key`, `Content-Type` and `User-Agent` belong to the SDK and raise `ValueError` (use `idempotency_key=` for the key). |
| `extra_query` | A mapping of query parameters added to this call, merged over the ones the SDK built. For a filter or `expand` the API supports and this SDK version has no keyword for yet. A `None` value is dropped; send a list filter as one comma-joined string. |
| `extra_body` | `POST` methods only. A mapping of top-level JSON body fields merged over the ones the SDK built from its keywords. A `None` value here is sent as JSON `null`. |

```python
refund = client.refunds.create(
    payment_id="pay_123",
    idempotency_key=f"refund-{order_id}",
    timeout=15.0,
    max_retries=5,
    extra_headers={"X-Request-Source": "support-console"},
)
```

`extra_query` and `extra_body` are the way to reach an API field newer than
the SDK without waiting for a release. The SDK does not check them: a field
the route does not take comes back as an `InvalidRequestError` with `code`
`parameter_unknown` and the field in `param`. They are typed `Any`, so prefer
the keyword once your SDK version has one.

```python
# Hypothetical fields, standing in for ones a later 1.x API adds.
client.customers.list(extra_query={"created_after": 1767225600})
client.customers.create(email="ada@example.com", extra_body={"preferred_locale": "nl"})
```

## One-shot (mandate-less) payments

A one-shot is a single charge with no subscription, mandate or renewals: the Stripe PaymentIntent shape, mapped onto Mollie. Create it, redirect to `redirect_url`, and settle terminal state via the `one_shot_payment.succeeded` / `.failed` webhook events.

```python
payment = client.one_shot_payments.create(
    customer_id=customer.id,
    amount_cents=2500,
    currency="EUR",
    payment_method="ideal",
    success_url="https://shop.example.com/thanks",
    refund_window_days=14,  # optional; 0 disables refunds, default is 30
)
print(payment.redirect_url)  # redirect the payer here

# Later, refund it within its window (omit amount_cents for a full refund):
client.refunds.create(one_shot_payment_id=payment.id)
# ...or refund part of it. A charge can carry several partials:
client.refunds.create(one_shot_payment_id=payment.id, amount_cents=500)

# List one customer's one-off charges, newest first (payments.list only
# lists subscription payments):
for charge in client.one_shot_payments.iter(customer_id=customer.id, status="paid"):
    print(charge.id, charge.amount_cents)
```

### Embedded one-shot payments

To take the payment inside your own page instead of redirecting to Mollie, create it with `ui_mode="embedded"` and no `payment_method`. The payer picks the method in the BillKit payment element, which you mount in the browser with the returned `client_secret` (`mountOneShotPaymentElement` in `@billkit-eu/js`, or `<OneShotPaymentElement/>` in `@billkit-eu/react`).

```python
payment = client.one_shot_payments.create(
    customer_id=customer.id,
    amount_cents=2500,
    currency="EUR",
    ui_mode="embedded",
    success_url="https://shop.example.com/thanks",
)
client_secret = payment.client_secret  # hand this to the browser, nothing else
```

The secret is short-lived (`expires_at`) and only on the create response; a retrieve returns it as `null`. `redirect_url` is `null` and `payment_method` stays `null` until the payer confirms. Fulfil from the same `one_shot_payment.succeeded` webhook as a hosted one-shot.

## Metered billing

A metered price charges for what was consumed. You report usage, and at each period close BillKit invoices the period's total and charges the stored mandate.

There are three ways to price a unit, and a price uses exactly one of them.

```python
# 1. Whole minor units: 5 cents per unit.
client.prices.create(
    product_id=product.id, amount_cents=5,
    currency="EUR", interval="month", usage_type="metered",
)

# 2. Finer than a minor unit. "0.02" is 0.02 CENTS, i.e. EUR 0.0002 per unit
#    -- the canonical per-API-call price, which no integer can express.
client.prices.create(
    product_id=product.id, unit_amount_decimal="0.02",
    currency="EUR", interval="month", usage_type="metered",
)

# 3. By bands. "graduated" prices the units inside each band; "volume" lets
#    the period total pick one band which then prices every unit. The same
#    table under the two modes is a different bill, so the mode is required.
client.prices.create(
    product_id=product.id, currency="EUR", interval="month",
    usage_type="metered", billing_scheme="tiered", tiers_mode="graduated",
    tiers=[
        {"up_to": 1000, "unit_amount_cents": 1},          # first 1,000 at EUR 0.01
        {"up_to": "inf", "unit_amount_decimal": "0.5"},   # then EUR 0.005
    ],
)
```

A band is `up_to` (an int, or `"inf"` on the last band) plus `unit_amount_cents`, `unit_amount_decimal` and/or `flat_amount_cents` (charged once for reaching the band). `billkit.PriceTierParam` types one, so a type checker catches the 0.x spellings `unit_amount` and `flat_amount`, which the API refuses.

**`unit_amount_decimal` is a string, and a `float` is refused.** Pass `str`, `int` or `Decimal`; a float raises `TypeError`. A float cannot hold 0.0002 exactly, so accepting one would work for the values that happen to round-trip and silently mis-price the ones that do not. The same rule applies inside a tier.

### Reporting usage, exactly once

```python
client.subscriptions.create_usage_record(
    sub.id,
    quantity=1200,
    identifier="job-2026-09-19T10:00Z",  # your id for what you are metering
)
```

Two dedupe mechanisms, for two different failures. The `Idempotency-Key` the SDK sends covers a retry of *that HTTP request*, including its own internal retries. `identifier` covers a retry of *your* call -- a job runner replaying a task, a queue delivering twice, your code re-invoking after its own timeout -- which reaches the API as a genuinely new request with a new key. A second report of the same identifier returns the first record unchanged rather than billing twice. If your reporting pipeline is at-least-once, `identifier` is the one that matters.

### Knowing what the next invoice will be

```python
summary = client.subscriptions.retrieve_usage_summary(sub.id)
summary.pending_quantity  # 3
summary.gross_cents       # 15
summary.will_charge       # False
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
paused = client.subscriptions.list(renewal_state=["paused"])

# Both filters take a list, sent comma-joined, and carry onto every page:
for sub in client.subscriptions.iter(status=["active", "past_due"], limit=100):
    ...
```

`status=["paused"]` is not an accepted value and raises `InvalidRequestError`. The same list form works for `disputes.list(status=)` and `events.list(type=)`; one string is sent as given, so `status="active,past_due"` still works.

## Clearing a field

On an update, an explicit `None` clears a field and omitting the keyword leaves the stored value alone. These keywords default to `billkit.NOT_GIVEN` rather than `None`, which is how the SDK tells the two apart. This applies to `products.update` (`description`, `default_price_id`, `marketing_features` empties the list), `prices.update` (`refund_window_initial_days` and `refund_window_renewal_days` drop the price's override), `customers.update` (`name`), `webhook_endpoints.update` (`description`), `coupons.update` (`max_redemptions` removes the cap, `redeem_by` removes the expiry, `applies_to_price_ids` lifts the price restriction, `min_amount_cents` lifts the minimum), `tax_rates.update` (`display_name`), `tenant.set_billing_profile` (the address fields and `registration_number`) and `tenant.set_portal_branding` (every field). Every other keyword still treats `None` as "not given" and leaves it out of the request, because the API refuses a null on a field it cannot clear with a 400 naming the field. A `metadata` update replaces the stored object whole, so `metadata={}` is how it is emptied.

```python
client.coupons.update(coupon.id, max_redemptions=None)  # remove the cap
client.customers.update(customer.id, email="new@example.com")  # name untouched
```

A `None` passed straight through from your own data therefore clears the field. When a value may be missing and you mean "leave it", omit the keyword.

## Retiring something, and deleting something

`delete()` exists on `customers`, `webhook_endpoints` and `test_clocks`, and it returns a `DeletedResource` (`{"id": ..., "object": ..., "deleted": True}`) rather than the object: it has left the API, so there is nothing to hand back. A deleted endpoint takes its delivery rows with it, because those are readable only through the endpoint that owns them; the events stay in `client.events`, which is the record of what you were sent.

The catalogue is retired through its update route instead, because it stays readable afterwards. Prices, products, tax rates and coupons take `active=False`. Each of them has to survive: subscriptions renew against a price by id, an invoice records the VAT percentage a tax rate produced, and a redeemed coupon is part of what a customer was charged.

`status="disabled"` on a webhook endpoint is the other half of the pair, not a substitute for deleting. It stops delivery and keeps the endpoint, its secret and its history, and it can be turned back on.

A price's amount, currency, interval and usage type are fixed at creation, because subscriptions renew against it by id. What `prices.update` accepts decides what happens next: `active`, `metadata`, `payment_methods`, the refund policy, and `tax_behavior` once while it is still `"unspecified"`. `active` moves both ways: it decides what new checkouts may buy, not what anyone was charged. Re-sending the value it already has is a no-op, so a retry is safe.

```python
# Stop selling a price. It stays readable; customers on it keep renewing.
archived = client.prices.update(price.id, active=False)
assert archived.active is False

# Stop sending to an endpoint, without losing its signing secret.
client.webhook_endpoints.update(endpoint.id, status="disabled")

# Remove a customer. Refused while they hold a subscription that can
# still charge them.
client.customers.delete(customer.id)  # -> DeletedResource(deleted=True, ...)
```

## Erasing a customer (GDPR)

`customers.purge` erases a customer's personal data: the email is replaced
with a placeholder, the name, metadata and VAT registration are cleared,
including from past events, and the customer 404s afterwards. Payments,
invoices and credit notes are kept for accounting. It cannot be undone, so it
requires `confirmed=True`, and without it the SDK raises before sending anything:

```python
purged = client.customers.purge(customer.id, confirmed=True)
assert purged.purged  # {"id": ..., "object": "customer", "deleted": True, "purged": True}
```

The answer is a `PurgedResource`, the same marker `delete()` returns plus
`purged`, not the customer.

## Test clocks

A test clock is virtual time for test mode. Create one, create customers on it, then advance it: trials end, renewals fall due and dunning runs for that clock's customers only, and the work is done before `advance()` returns.

```python
import time

clock = client.test_clocks.create(frozen_time=int(time.time()), name="Trial ends")
customer = client.customers.create(email="ada@example.com", test_clock_id=clock.id)
# ...start a trial subscription for the customer, then jump past it:
client.test_clocks.advance(clock.id, frozen_time=clock.frozen_time + 15 * 86_400)

client.test_clocks.delete(clock.id)  # -> DeletedResource; its customers return to real time
```

`test_clock_id` is fixed when the customer is created, and reads back as `customer.test_clock_id`. `frozen_time` on `advance()` must be later than the clock's current time. Every route answers a live key with an `InvalidRequestError`. The same verbs exist on `AsyncBillKit`.

Every id argument must be non-empty: `client.customers.retrieve("")` raises `ValueError` before any request is made.

## Invoice and credit-note PDFs

```python
from pathlib import Path

Path("invoice.pdf").write_bytes(client.invoices.retrieve_pdf("inv_123"))
Path("credit-note.pdf").write_bytes(client.credit_notes.retrieve_pdf("cn_123"))
```

Returns the raw bytes. S3-backed deployments answer with a redirect to a presigned URL, which the SDK follows under its own timeout and retry policy, so both storage adapters look the same from here. The API key is never sent to the storage host, because the presigned URL carries its own credential. A deployment with PDF rendering disabled answers `501`, which surfaces at once (it is not retried) as a `ServerError` with `code == "rendering_pending"`; `retrieve()` still gives you the structured document to render yourself.

## Async

```python
from billkit import AsyncBillKit

async with AsyncBillKit(api_key="bk_test_...") as client:
    customer = await client.customers.create(email="ada@example.com")
```

## Configuration

```python
import httpx
from billkit import BillKit, RetryPolicy

client = BillKit(
    api_key="bk_test_...",  # or set BILLKIT_API_KEY
    base_url="https://api.billkit.eu",  # override for self-hosted
    timeout=30.0,  # seconds, or an httpx.Timeout; None disables it
    retry_policy=RetryPolicy(
        max_attempts=5,
        max_retry_after_seconds=10.0,  # cap 429 Retry-After sleeps
    ),
    httpx_client=httpx.Client(proxy="http://proxy.internal:3128"),  # optional
)
```

`httpx` is part of this SDK's public surface: `httpx_client=` takes an
`httpx.Client` (`httpx.AsyncClient` for `AsyncBillKit`) and `timeout=` takes an
`httpx.Timeout`. That is why the dependency is capped below httpx 1.0. The SDK
never closes a client you passed in. A `timeout=` given to the constructor
applies to every request through it; leave `timeout` out and your client's own
default stands. The default for a client the SDK builds is 30 seconds, 10 of
them to connect (`billkit.DEFAULT_TIMEOUT`).

`AsyncBillKit` runs under asyncio or trio: its retry backoff sleeps through
`anyio`, which httpx already depends on.

## Idempotency and retries

The SDK auto-generates an `Idempotency-Key` for every mutating call, so 5xx and short `Retry-After` 429 retries are safe (a 501 is permanent and is not retried): the server replays the original response when an earlier attempt completed. Pass `idempotency_key=` to coalesce retries across process restarts. A blank or whitespace-only `idempotency_key` counts as not passed, and the SDK generates one.

`409 idempotency_in_progress` is retried too. It means an earlier request carrying the same key is still in flight, which is the one 4xx where giving up is the dangerous answer: that request may already have charged the customer, and the obvious workaround, retrying with a *fresh* key, is exactly what turns one charge into two. The retry reuses the original key, so it either loses the race again or replays the first call's result. Every other 409 (`idempotency_key_in_use`, a conflicting subscription state) fails immediately, because retrying can only repeat it.

## Errors

```python
from billkit import BillKit, BillKitError, NotFoundError, RateLimitError

client = BillKit(api_key="bk_test_...")
try:
    customer = client.customers.retrieve("cus_doesnt_exist")
except NotFoundError:
    print("Customer is gone")
except RateLimitError as exc:
    print(f"Rate limited; retry in {exc.retry_after}s")
except BillKitError as exc:
    print(f"BillKit error {exc.status_code}: {exc.message} (request {exc.request_id})")
```

The same names mean the same thing in every BillKit SDK:

```text
BillKitError                    every error from a request or a webhook check
├── APIError                    the API answered with an HTTP error
│   ├── InvalidRequestError     400 and any other 4xx not below
│   ├── AuthenticationError     401
│   ├── PaymentError            402, a refused charge or a suspended account
│   ├── PermissionDeniedError   403
│   ├── NotFoundError           404
│   ├── ConflictError           409
│   ├── RateLimitError          429, with retry_after
│   └── ServerError             5xx
├── APIConnectionError          no answer: network, DNS, TLS
│   └── APITimeoutError         no answer in time
└── WebhookVerificationError    a delivery failed verification
```

An `APIError` carries `status_code`, `message`, `type`, `code`, `param`, `reason`, `request_id`, `raw_body` (the decoded error body, or its text when it was not JSON) and `headers`. The same attributes exist on every `BillKitError`, as `None` when there was no HTTP answer, so a handler typed against the base class can read them. Every error pickles with all its attributes. An argument mistake caught before anything is sent (a blank id, `max_retries=-1`, a reserved header in `extra_headers`) raises `ValueError` or `TypeError`, not a `BillKitError`. Known `code`, `type` and `reason` values are listed in `billkit.types.ErrorCode`, `ErrorType` and `ErrorReason`; all three are open.

`PaymentError` (402) has two causes, told apart by `code`: `payment_refused`, when the payment provider declined the charge (a refund it would not make, say) and `message` carries its reason, and `tenant_suspended`, when the BillKit account behind the key is suspended and the call would move money. Neither is retried.

`BillKit(api_key="")` and `RetryPolicy(max_attempts=0)` raise `ValueError` at construction: an empty key is usually an unset variable, and a policy of zero attempts would never send a request. Leave `api_key` out (or pass `None`) to read `BILLKIT_API_KEY`; use `max_attempts=1` for no retries.

**`APIError` means any HTTP error, not "the server failed".** Catch `ServerError` for that. (In 0.x `APIError` was the parent of 5xx only.)

The class is chosen by **HTTP status**, not by the envelope's `type`.

The status is the field nothing in between can get wrong. An unmatched path is a 404 with `type: "invalid_request_error"` and `code: "route_not_found"`, so mapping on `type` would raise an invalid-request error for what is a missing resource; and a 4xx whose body says `api_error` (as a proxy in front of the API can send) would become a `ServerError` and tell you BillKit had broken when the request was at fault. The envelope's `type`, `code`, `param` and `reason` are all still on the raised object if you want them.

## Logging

The SDK is **silent by default**. It owns one logger, `logging.getLogger("billkit")`, with a `NullHandler` attached, and it never calls `basicConfig`, never sets a level, and never adds a handler to a logger it doesn't own. Your logging config is yours.

Turn it on from your application:

```python
import logging

logging.basicConfig()
logging.getLogger("billkit").setLevel(logging.DEBUG)
```

```
DEBUG:billkit:BillKit request POST https://api.billkit.eu/v1/customers (attempt 1/4)
DEBUG:billkit:BillKit response POST https://api.billkit.eu/v1/customers -> 503 in 84ms (request_id=req_9f2a)
WARNING:billkit:BillKit retrying POST https://api.billkit.eu/v1/customers after HTTP 503 (attempt 1) in 500ms
DEBUG:billkit:BillKit request POST https://api.billkit.eu/v1/customers (attempt 2/4)
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

So when you opt this SDK in, it raises the `httpx` and `httpcore` loggers to `WARNING`, but **only** if you have not set a level on them yourself, and **only** for the `httpx` client the SDK created. Both exceptions are deliberate:

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
from billkit import WebhookSignature, WebhookVerificationError, types

# In your FastAPI / Flask / Django handler:
try:
    event = WebhookSignature.verify(
        payload=request.body,  # the RAW bytes, exactly as received
        signature_header=request.headers.get("BillKit-Signature"),
        secret=os.environ["BILLKIT_WEBHOOK_SECRET"],
    )
except WebhookVerificationError:
    return Response(status_code=400)

match event:
    case types.SubscriptionCreatedEvent():
        handle_new_subscription(event.data)  # a typed billkit.types.Subscription
    case types.InvoicePaidEvent(data=invoice):
        mark_paid(invoice.id, invoice.total_cents)
    case _:
        pass  # a type you do not handle, or one newer than this SDK: acknowledge it
```

The verifier enforces a 5-minute timestamp tolerance (replay protection) and constant-time HMAC compare. Pass `tolerance_seconds=` to customise. It raises `WebhookVerificationError` (a `BillKitError`) when the header is missing, malformed or stale, when no signature matches, or when the signed body is not a JSON object.

`verify` returns the event's own class when this SDK version knows its type, one per event type in `billkit.types` (for example `SubscriptionUpdatedEvent`), each with `data` typed to match. A type this version does not know comes back as a plain `billkit.Event` whose `data` is a `BillKitObject`, readable both ways. **Ignore a type you do not handle and answer 2xx**: a non-2xx makes BillKit retry a delivery you were never going to process. `events.retrieve`, `events.list` and `events.iter` return the same classes. `event.to_dict()` gives the plain JSON, for a queue or a function typed `dict[str, Any]`.

`data` is the object itself, not a `data.object` wrapper. `previous_attributes` sits **beside** `data`, and it is always present: on `subscription.updated`, and on the specific subscription events that fire alongside it, it holds the fields that changed with their values before the change; on every other event it is `None`. Test it for `None`, not for presence:

```python
if isinstance(event, types.SubscriptionUpdatedEvent) and event.previous_attributes:
    before = event.previous_attributes.get("status")
    if before is not None:
        print(f"status went {before} -> {event.data.status}")
```

`EventType` is a `StrEnum` of every event type this SDK version knows, and `EVENT_TYPES` lists them for `webhook_endpoints.create(enabled_events=...)`. Compare against the members or use `is_known_event_type()`; do not call `EventType(event.type)`, which raises on a type it does not know. Nothing in the SDK raises on one.

## Reference

Every method below exists on both clients, with the same signature (`await`
it on `AsyncBillKit`), and takes the per-request options above. Ids are
positional; everything else is keyword-only and named as the API names it.
Models are in `billkit.types`; `Page[X]` below means `ListObject[X]`.

| Method | Returns |
|---|---|
| `api_keys.create(label=, scopes=)` | `ApiKey`, with the secret (shown once) |
| `api_keys.retrieve(id)` / `revoke(id)` | `ApiKey` |
| `api_keys.list()` / `iter()` | `Page[ApiKey]` / iterator |
| `customers.create(email=, name=, country_code=, metadata=, test_clock_id=)` | `Customer` |
| `customers.retrieve(id)` / `update(id, ...)` | `Customer` |
| `customers.set_vat_number(id, vat_number=, country_code=)` | `Customer`; `vat_number=None` clears |
| `customers.delete(id)` | `DeletedResource` |
| `customers.purge(id, confirmed=True)` | `PurgedResource` |
| `customers.list(provisional=, expand=)` / `iter(...)` | `Page[Customer]` / iterator |
| `products.create(name=, ...)` / `retrieve(id, expand=)` / `update(id, ...)` | `Product` |
| `products.list(expand=)` / `iter(...)` | `Page[Product]` / iterator |
| `prices.create(product_id=, currency=, interval=, ...)` / `retrieve(id)` / `update(id, ...)` | `Price` |
| `prices.list(product_id=)` / `iter(...)` | `Page[Price]` / iterator |
| `checkout_sessions.create(price_id=, success_url=, cancel_url=, ...)` / `retrieve(id)` | `CheckoutSession` |
| `one_shot_payments.create(customer_id=, amount_cents=, currency=, success_url=, ...)` / `retrieve(id)` | `OneShotPayment` |
| `one_shot_payments.list(customer_id=, status=)` / `iter(...)` | `Page[OneShotPayment]` / iterator |
| `subscriptions.retrieve(id, expand=)` | `Subscription` |
| `subscriptions.list(customer_id=, status=, renewal_state=, expand=)` / `iter(...)` | `Page[Subscription]` / iterator |
| `subscriptions.cancel(id)` / `pause(id)` / `resume(id)` / `reactivate(id)` | `Subscription` |
| `subscriptions.preview_update(id, target_price_id=)` | `PlanChangePreview` |
| `subscriptions.update(id, target_price_id=)` | `Subscription` |
| `subscriptions.reauthorize_payment_method(id, return_url=)` | `Reauthorization` |
| `subscriptions.create_usage_record(id, quantity=, identifier=, ...)` | `UsageRecord` |
| `subscriptions.list_usage_records(id, invoice_id=)` / `iter_usage_records(...)` | `Page[UsageRecord]` / iterator |
| `subscriptions.retrieve_usage_summary(id)` | `UsageSummary` |
| `refunds.create(payment_id= / subscription_id= / one_shot_payment_id=, amount_cents=, reason=)` / `retrieve(id)` | `Refund` |
| `refunds.list()` / `iter()` | `Page[Refund]` / iterator |
| `disputes.retrieve(id)` | `Dispute` |
| `disputes.list(status=, payment_id=)` / `iter(...)` | `Page[Dispute]` / iterator |
| `invoices.retrieve(id, expand=)` / `void(id, reason=)` | `Invoice` |
| `invoices.retrieve_pdf(id)` | `bytes` |
| `invoices.send_email(id)` | `InvoiceEmail` |
| `invoices.list(customer_id=, subscription_id=, payment_id=, status=, expand=)` / `iter(...)` | `Page[Invoice]` / iterator |
| `credit_notes.retrieve(id)` | `CreditNote` |
| `credit_notes.retrieve_pdf(id)` | `bytes` |
| `credit_notes.list(invoice_id=, customer_id=)` / `iter(...)` | `Page[CreditNote]` / iterator |
| `payments.retrieve(id, expand=)` | `Payment` |
| `payments.retrieve_provider(id)` | `ProviderPayload` |
| `payments.list(customer_id=, expand=)` / `iter(...)` | `Page[Payment]` / iterator |
| `coupons.create(code=, percent_off= / amount_off_cents=, ...)` / `retrieve(id)` / `update(id, ...)` | `Coupon` |
| `coupons.validate(code=, price_id=, amount_cents=, currency=)` | `CouponValidation` |
| `coupons.list()` / `iter()` | `Page[Coupon]` / iterator |
| `tax_rates.create(country_code=, rate_basis_points=, ...)` / `retrieve(id)` / `update(id, ...)` | `TaxRate` |
| `tax_rates.list()` / `iter()` | `Page[TaxRate]` / iterator |
| `webhook_endpoints.create(url=, enabled_events=, ...)` / `retrieve(id)` / `update(id, ...)` / `rotate_secret(id)` | `WebhookEndpoint` |
| `webhook_endpoints.delete(id)` | `DeletedResource` |
| `webhook_endpoints.list()` / `iter()` | `Page[WebhookEndpoint]` / iterator |
| `webhook_endpoints.list_event_types()` | `EventTypeList` |
| `webhook_endpoints.list_deliveries(id)` / `iter_deliveries(id)` | `Page[WebhookDelivery]` / iterator |
| `webhook_endpoints.retrieve_delivery(id, delivery_id)` / `redeliver(id, delivery_id)` | `WebhookDelivery` |
| `events.retrieve(id)` | `Event`, as its typed subclass when known |
| `events.list(type=, expand=)` / `iter(...)` | `Page[Event]` / iterator |
| `billing_portal_sessions.create(subscription_id=, return_url=, ...)` | `BillingPortalSession` |
| `billing_portal_sessions.revoke(id)` | `BillingPortalSessionRevocation` |
| `audit_logs.retrieve(id)` | `AuditLog` |
| `audit_logs.list(action=, resource_type=, resource_id=, actor_id=)` / `iter(...)` | `Page[AuditLog]` / iterator |
| `test_clocks.create(frozen_time=, name=)` / `retrieve(id)` / `advance(id, frozen_time=)` | `TestClock` |
| `test_clocks.delete(id)` | `DeletedResource` |
| `test_clocks.list()` / `iter()` | `Page[TestClock]` / iterator |
| `tenant.retrieve_capabilities()` | `TenantCapabilities` |
| `tenant.retrieve_portal_branding()` / `set_portal_branding(...)` | `TenantPortalBranding` |
| `tenant.retrieve_billing_profile()` / `set_billing_profile(country_code=, ...)` | `TenantBillingProfile` |
| `tenant.rotate_provider_credential(api_key=, mode=)` | `TenantProviderCredential` |
| `tenant.export()` | `bytes`, the whole account as one JSON document |
| `WebhookSignature.verify(payload=, signature_header=, secret=, tolerance_seconds=)` | `Event` |

## Development

```bash
uv sync --all-extras --dev
uv run ruff check src tests scripts
uv run mypy src tests scripts
uv run pytest
```

`src/billkit/types.py` is generated from the API's OpenAPI document by
`scripts/generate_models.py`; do not edit it by hand.

## License

Apache-2.0. See [LICENSE](LICENSE).
