# Changelog

All notable changes to the BillKit Python SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each BillKit SDK is versioned on its own. The server SDKs (Python, Node, PHP)
happened to share their 0.x numbers because they were released together, and
they reach 1.0.0 together for the API's 1.0 contract; after that a fix in one
does not bump the others, so the numbers will drift apart again. Compare
features, not version numbers.

Published to PyPI as `billkit-eu`; the import name is `billkit`.

## [Unreleased]

## [1.1.1] - 2026-10-19

### Changed
- Generated types regenerated against the current public spec: the
  `stripe_import.connected`, `stripe_import.started` and
  `subscription.imported` audit actions and the `import_not_activated`
  cancellation reason.

## [1.1.0] - 2026-10-19

### Added

- `Price` and `PriceSummary` carry `payment_methods_from_account`: `True`
  when the price follows the account's payment methods, `False` when it has
  its own list. When it is `True`, `payment_methods` lists the account's
  methods (every recurring method the workspace has not switched off).
- `prices.update(price_id, follow_account_payment_methods=True)` drops the
  price's own `payment_methods` list (sent as a JSON null), so the price
  follows the account's payment methods again. `payment_methods=None` still
  means "leave it unchanged", as in 1.0.
- Generated from API contract 1.1.0: the audit log `AuditLogAction` enum gains
  `tenant.test_mode_email_enabled`, `tenant.test_mode_email_disabled`,
  `tenant.invoice_pdf_attach_enabled`, `tenant.invoice_pdf_attach_disabled`
  and `tenant.payment_methods_updated`.

### Changed

- A price created without `payment_methods` now follows the account's
  payment methods instead of defaulting to `["creditcard"]` (an API change in
  contract 1.1.0; the SDK never sent a default and still sends no key).
- Every field on the typed responses in `billkit.types` carries the API
  reference's description as a docstring, so an editor shows what it means.
  No type or behaviour changed.

## [1.0.0] - 2026-10-14

The first stable release, built against the API's 1.0 contract. From here the
package follows semantic versioning strictly: a 1.x release never breaks code
written against an earlier 1.x. Getting there takes one deliberate break from
0.x, every part of which is listed under **Upgrading from 0.x** below with the
code before and after.

### The 1.0 API contract

The API froze its `/v1` contract for 1.0; these are the parts a caller sees.

- Request validation is a `400` everywhere (`InvalidRequestError`); the API no longer
  answers `422`, though one would still map to `InvalidRequestError`.
- New error codes in `billkit.types.ErrorCode`: `parameter_unknown` (an unknown body
  field or query parameter, named in `param`), `route_not_found` (404),
  `method_not_allowed` (405), `state_conflict` (409, replacing a 400
  `parameter_invalid` for a state refusal such as pausing a canceled
  subscription; `reason` is unchanged) and `mode_mismatch` (403).
- An unmatched route is a 404 with type `invalid_request_error` and code
  `route_not_found`; the status still picks the class, so it is the
  not-found error.
- A checkout session's `object` is `checkout.session` (was
  `checkout_session`).
- `subscriptions.create_usage_record` is answered with `200` (was `201`); the return value is
  unchanged.
- API key, audit log and webhook delivery ids are prefixed: `ak_`, `al_`,
  `whd_`.
- `next_charge_amount_cents` is filled on subscription list, retrieve and
  mutation responses and on every `subscription.*` webhook, not only on
  retrieve.
- `expand` needs the expanded resource's read scope; without it the call
  is a `403 insufficient_scope` with `param` `expand`.
- A payment the provider refuses is a `402` with type `payment_error` and code
  `payment_refused` (was `invalid_request_error` / `parameter_invalid` with
  `reason` `payment_refused`). It raises `PaymentError`.
- A missing required field is a `400` with code `parameter_missing`, naming the
  field in `param`.
- `refunds.create` on a payment whose state rules a refund out (not paid,
  outside its refund window) is a `409` `ConflictError` with code
  `state_conflict` (was a `400`).
- Renamed fields, in requests, responses and webhook payloads alike:
  `payment_method` on checkout sessions and one-off payments (was `method`),
  `reversed_at` on disputes (was `reversed`), `test_clock_id` on customers
  (was `test_clock`).
- `subscriptions.list` `status` and `renewal_state`, `disputes.list` `status`
  and `events.list` `type` are list filters: pass a list and it is sent
  comma-joined (`status=["active", "past_due"]`). One string, commas and all,
  is still sent as given. `invoices.list` and `one_shot_payments.list` take
  exactly one `status`, from a closed set.
- `percent_off` on a coupon takes up to two decimal places (more than 0, at
  most 100): `coupons.create(percent_off=...)` accepts an `int`, `float` or
  `Decimal`, and `Coupon.percent_off` / `CouponValidation.percent_off` are
  typed `float`: a fractional percent reads back as a `float` (`12.5`), a
  whole one as an `int` (`99`).
- `OneShotPayment.metadata` is always an object, `{}` when none was set.
- `BillingPortalSession.subscription_id` (and the revocation's) is nullable.
- `customers.purge` returns a `PurgedResource`
  (`{"id", "object": "customer", "deleted": True, "purged": True}`) instead of
  the erased customer, whose placeholder email would read as live data.
- `invoice.*` events carry the invoice's real `lines` in `data`.
- `InvoiceStatus` no longer lists `draft`: the API never issues one.
- `ProviderPayload.redacted` says when the stored provider payload was redacted.
- Every operation documents a `500` with the error envelope.

### Hardening

- `WebhookSignature.verify` raises `ValueError` for a `tolerance_seconds`
  that is not a finite number of seconds, 0 or more. `nan` compared false
  against every timestamp and so accepted a replay of any age.
- A path id of `.` or `..` raises `ValueError` before any request.
  Percent-encoding leaves both alone and httpx resolved them as dot
  segments, so `customers.retrieve("..")` requested `GET /v1/`.
- The sdist carries `scripts/generate_models.py`, which the tests import, so
  the suite collects and passes from an unpacked sdist.

### Added
- **Typed responses.** Every method returns a model from `billkit.types`
  (`Customer`, `Subscription`, `Invoice`, ...), generated from the API's public
  OpenAPI document by `scripts/generate_models.py`. Every model is a
  `billkit.BillKitObject`: a Pydantic v2 model that is also a read-only
  `Mapping`, so `sub.status`, `sub["status"]`, `sub.get("status")`,
  `"status" in sub` and `dict(sub)` all work, permanently, with no deprecation
  warning on either style. `to_dict()` returns plain JSON types recursively,
  and so do `model_dump()`, `model_dump_json()` and FastAPI's
  `jsonable_encoder`: exactly the keys the API sent, with an event's `data`
  and a page's items intact.
- Objects, pages and every exception pickle, so they cross process
  boundaries (multiprocessing, task queues, caches).
- **Forward compatibility by construction.** Responses are not validated: a
  field this version does not know is kept (readable both ways, kept by
  `to_dict()`), an enum value it does not know is kept as the plain string, and
  a value of an unexpected type is kept as sent, so the API growing can never
  turn a successful call into an exception.
- Open-enum constants: `StrEnum` classes listing the values this version knows,
  for comparison only (`sub.status == SubscriptionStatus.PAST_DUE`). The fields
  stay typed `str`.
- `billkit.ListObject[T]` for list pages: iterating a page yields its items,
  `len(page)` counts them and `item in page` looks for one, as in
  stripe-python. A page is not a `Mapping`; `page["data"]`, `page["has_more"]`,
  `page["object"]`, `page.get("has_more")` and `"has_more" in page` still work
  with a string key.
- **Typed webhook events.** `WebhookSignature.verify` returns the event's own
  class (`SubscriptionUpdatedEvent`, `InvoicePaidEvent`, ... one per event type,
  each with a typed `data`), or a plain `billkit.Event` whose `data` is a
  `BillKitObject` for a type this version does not know. `events.retrieve`,
  `events.list` and `events.iter` return the same classes.
  `event.previous_attributes` is always present: `dict` or `None`.
- **Per-request options on every method**, keyword-only: `timeout` (seconds or
  `httpx.Timeout`; `None` for none), `max_retries` (`0` is one attempt) and
  `extra_headers`, plus `idempotency_key` on every mutating method. The
  idempotency key is still fixed once per call, before the retry loop.
  `extra_headers` cannot set `Authorization`, `Idempotency-Key`,
  `Content-Type` or `User-Agent` (in any case): those raise `ValueError`.
- `extra_query=` on every method and `extra_body=` on every `POST` method:
  query parameters and top-level body fields this SDK version has no keyword
  for, merged over the ones it built, so a field a later 1.x API adds is
  usable without upgrading. `iter()` carries `extra_query` onto every page.
  The SDK forwards them unchecked; a field the route does not take is the
  API's `400 parameter_unknown`.
- `iter()` accepts every filter its `list()` accepts, carries them onto every
  page, and takes `starting_after` to resume from a cursor. Its page size is
  `limit`, the API's own parameter name (it was `page_size`).
- `billkit.NOT_GIVEN` / `billkit.NotGiven`, the public "keyword not passed"
  sentinel, replacing the private `_Unset` in signatures.
- `billkit.PriceTierParam`, a `TypedDict` for a price tier, so a type checker
  catches a misspelt or retired tier key.
- `APITimeoutError` (a subclass of `APIConnectionError`) for a request that got
  no answer in time; `APIError.headers` with the response headers.
- `billkit.DEFAULT_TIMEOUT` is exported.
- `test_clocks` on both clients: `create`, `retrieve`, `advance`, `delete`,
  `list` and `iter` over `/v1/test_clocks`, plus
  `customers.create(test_clock_id=...)` to attach a customer to a clock. Test
  mode only.
- `billkit.EventType` (a `StrEnum`, e.g. `EventType.INVOICE_PAID`),
  `EVENT_TYPES` and `is_known_event_type()`, generated from the API's event
  catalogue. An unknown type is still never an error: a newer API can deliver
  one this list does not name, so use `is_known_event_type()` rather than
  `EventType(value)`.
- `BillKitError.reason`, the envelope's finer-grained cause, which was only
  reachable through `raw_body`. A `409 operation_pending` from
  `subscriptions.cancel`, `pause` or `update` while a renewal payment is still
  settling carries `reason == "renewal_processing"`; a blank `Idempotency-Key`
  sent by hand is a `400 parameter_invalid` with
  `reason == "idempotency_key_blank"`.
- `coupons.create(currency=...)` (the currency of a fixed discount and of
  `min_amount_cents`; required with either when the account has several
  currencies) and `coupons.validate(currency=...)`, where a coupon in another
  currency comes back `valid: False` with
  `reason == "coupon_currency_mismatch"`.
- Python 3.14 support; classifier `Development Status :: 5 - Production/Stable`.
- `PaymentError`, raised for a 402: `code` is `payment_refused` when the
  payment provider declined a charge or refund (its reason is in `message`),
  or `tenant_suspended` when the account behind the key is suspended. A 402
  was an `InvalidRequestError` before, which told the caller to change a
  request that was not at fault. Not retried.

### Changed
- The exception hierarchy is the one every BillKit SDK now shares:
  `BillKitError` > `APIError` (any HTTP error response) > `InvalidRequestError`
  (400, 422, other 4xx), `AuthenticationError` (401), `PaymentError` (402),
  `PermissionDeniedError`
  (403), `NotFoundError` (404), `ConflictError` (409), `RateLimitError` (429),
  `ServerError` (5xx); `BillKitError` > `APIConnectionError` >
  `APITimeoutError`; `BillKitError` > `WebhookVerificationError`.
- A `timeout=` passed to the client now applies through an injected
  `httpx_client` too. It was silently ignored there. Leave it out and an
  injected client keeps its own default.
- `coupons.create` takes `percent_off` or `amount_off_cents`; `discount_type`
  is optional (the API infers it) and `duration` defaults server-side to
  `"once"`.
- `tenant.set_portal_branding`: an explicit `None` now clears the field (sent
  as a JSON null), as the API documents; omit a keyword to leave it alone.
- `expand=` takes any sequence of strings, and a bare string means one
  relation.
- Dependencies: `pydantic>=2.7,<3` is new; `httpx` is now `>=0.28,<1`, because
  `httpx_client=` and `httpx.Timeout` are part of this package's public surface
  and httpx 1.0 is expected to break them.
- `BillKit(api_key="")` (or whitespace) raises `ValueError` at construction
  instead of sending an empty bearer token and failing every call with a 401.
  `None`, or leaving the keyword out, still reads `BILLKIT_API_KEY`.
- `RetryPolicy(max_attempts=0)` (or less) raises `ValueError`. Zero attempts
  never sent the request at all; `max_attempts=1` is "no retries".
- The async retry backoff sleeps with `anyio.sleep` instead of
  `asyncio.sleep`, so `AsyncBillKit` retries under trio instead of failing on
  the first retry. `anyio` was already installed with httpx and is now a
  declared dependency (`>=3.7,<5`).
- An error response with no envelope reports `type` as the API's own vocabulary
  (`not_found_error`, `conflict_error`) instead of `invalid_request_error` and
  `conflict`.

### Removed
- `PermissionError` (shadowed the builtin) and `ResourceMissingError`; see the
  renames below.
- `tenant.capabilities()`, `tenant.portal_branding()` and
  `tenant.billing_profile()`, renamed to `retrieve_*`.
- `billkit.WebhookEvent`, the `TypedDict` for a delivery body;
  `billkit.Event` and its subclasses replace it.
- `coupons.create(discount_value=)`, gone from the API.

### Fixed
- A 2xx response whose body is not JSON (a proxy or captive portal answering
  in the API's place) raises `APIError` with the status, request id, headers
  and the text on `raw_body`. It used to come back as an empty object, so the
  caller carried on as if the call had worked. An empty body is still an
  object with no keys.
- `raw_body` on an error whose body is not JSON (a proxy's HTML 502) is the
  body text. It was `None`, which threw away the one clue to what answered.
- A `501 Not Implemented` is raised on the first attempt. It was retried like
  any other 5xx, though the answer cannot change.
- `WebhookSignature.verify` accepts only ASCII digits (at most twelve) in
  `t=`. A long run of digits raised `OverflowError` instead of
  `WebhookVerificationError`, and `int()` also let through a sign,
  underscores and non-ASCII digits. An empty or whitespace-only `secret`
  raises `ValueError` before anything is verified, instead of checking the
  signature against an empty key.
- A blank or whitespace-only `idempotency_key` is treated as unset and
  replaced with a generated key. It used to send an empty `Idempotency-Key`
  header while the SDK still retried the POST; the API now refuses a blank
  key with a 400.
- An empty or whitespace-only id (`customers.retrieve("")`) raises
  `ValueError` before any request, instead of calling `/v1/customers/`.
- The README webhook example tested `"status" in event.get("previous_attributes", {})`,
  which raises `TypeError` now that the key is always present and usually
  `null`.
- `iter()` dropped `expand` on six resources and `provisional` on customers.

### Upgrading from 0.x

Most code keeps working: key access on responses, every method name except the
three `tenant` reads, and every keyword except the ones below. Work through
this list in order; the first two items are the ones that fail silently.

**1. `except APIError` now catches every HTTP error, not just 5xx.** In 0.x
`APIError` was the parent of `ServerError` only. A handler that treated it as
"the server failed, retry later" now also swallows 400s, 404s and 409s. Catch
`ServerError` for the old meaning.

```python
# 0.x
TRANSIENT = (billkit.APIConnectionError, billkit.APIError, billkit.RateLimitError)
# 1.0
TRANSIENT = (billkit.APIConnectionError, billkit.ServerError, billkit.RateLimitError)
```

**2. Responses are objects, not dicts.** `obj["id"]`, `obj.get("x")`, `"x" in
obj`, `dict(obj)` and `{**obj}` all still work. What does not:

```python
# 0.x                                   # 1.0
isinstance(customer, dict)              isinstance(customer, Mapping)   # collections.abc
customer == {"id": "cus_1", ...}        customer.to_dict() == {"id": "cus_1", ...}
json.dumps(customer)                    json.dumps(customer.to_dict())
customer["name"] = "Ada"                data = customer.to_dict(); data["name"] = "Ada"
for key in page: ...                    for key in page.to_dict(): ...  # iterating a page yields items
len(page)                               len(page.to_dict())             # len(page) counts items
dict(page)                              page.to_dict()                  # a page is not a Mapping
isinstance(page, Mapping)               isinstance(page, ListObject)
page["data"], page.get("has_more")      unchanged
```

Two objects are equal when they are the same class and their `to_dict()` is
equal, so an object where the API sent `"discount": null` differs from one
where `discount` was absent.

A field the API adds after this release is readable as `obj["new_field"]`,
which is the spelling to use under a type checker; `obj.new_field` works at
runtime but is not declared. A spec field whose name would shadow a model
method (`items`, `keys`, `values`, `get`, `to_dict`, `copy`, `dict`, `json`,
`model_*`) or is a Python keyword is declared with a trailing underscore
(`obj.items_`) and keeps its wire name as a key (`obj["items"]`).

A nested object (`sub["customer"]` with `expand=["customer"]`, `event["data"]`)
is an object too; plain JSON values (`metadata`, `previous_attributes`, unknown
fields) are still `dict` and `list`. A function annotated to return
`dict[str, Any]` that returns an SDK result now needs `.to_dict()`, or a
`Mapping[str, Any]` annotation, or the model type.

**3. Renamed exceptions.**

```python
# 0.x
from billkit import PermissionError, ResourceMissingError
# 1.0
from billkit import PermissionDeniedError, NotFoundError
```

Constructing one in a test still takes the message first and the fields as
keywords; `status_code` is now required on every `APIError`:
`NotFoundError("gone", code="resource_missing", status_code=404)`.

Every error from a request is a `BillKitError`. An argument mistake caught
before anything is sent (a blank id, `max_retries=-1`, a reserved header in
`extra_headers`) raises `ValueError` or `TypeError` instead.

**4. Renamed tenant reads.**

```python
# 0.x                              # 1.0
client.tenant.capabilities()       client.tenant.retrieve_capabilities()
client.tenant.portal_branding()    client.tenant.retrieve_portal_branding()
client.tenant.billing_profile()    client.tenant.retrieve_billing_profile()
```

**5. `customers.purge` needs an explicit confirmation.** 0.x sent the API's
`confirmed: true` on your behalf. Without `confirmed=True` the SDK now raises
`ValueError` (or `TypeError`, if the keyword is missing) before any request.

```python
# 0.x
client.customers.purge("cus_123")
# 1.0
client.customers.purge("cus_123", confirmed=True)
```

**6. Coupons.**

```python
# 0.x
client.coupons.create(code="TEN", discount_type="percent", discount_value=10, duration="once")
client.coupons.create(code="FIVER", discount_type="fixed_cents", discount_value=500, duration="once")
# 1.0
client.coupons.create(code="TEN", percent_off=10)
client.coupons.create(code="FIVER", amount_off_cents=500, currency="EUR")
```

Read `coupon.percent_off` / `coupon.amount_off_cents`; `discount_type` reads
`"percent"` or `"fixed_amount"`.

**7. Price tiers.** The API names every money field `*_cents`:

```python
# 0.x
tiers=[{"up_to": 1000, "unit_amount": 1}, {"up_to": "inf", "unit_amount_decimal": "0.5", "flat_amount": 500}]
# 1.0
tiers=[{"up_to": 1000, "unit_amount_cents": 1}, {"up_to": "inf", "unit_amount_decimal": "0.5", "flat_amount_cents": 500}]
```

**8. Webhooks.** `verify` returns a typed event instead of a `dict`, and
`WebhookVerificationError` is now a `BillKitError` (it was a bare
`Exception`), so an `except BillKitError` around `verify` now catches it. A
correctly signed body that is not a JSON object now raises
`WebhookVerificationError` rather than being returned.

```python
# 0.x
event = WebhookSignature.verify(payload=body, signature_header=sig, secret=secret)
if event["type"] == "subscription.updated" and "status" in event.get("previous_attributes", {}):
    old = event["previous_attributes"]["status"]

# 1.0
event = WebhookSignature.verify(payload=body, signature_header=sig, secret=secret)
if isinstance(event, types.SubscriptionUpdatedEvent) and event.previous_attributes:
    old = event.previous_attributes.get("status")
    new = event.data.status
# Need the plain dict, for a queue or a function typed dict[str, Any]?
payload = event.to_dict()
```

`billkit.WebhookEvent` is gone. If you annotated with it, remove the import
and use `billkit.Event` (or the specific event class) instead:

```python
# 0.x
from billkit import WebhookEvent
def handle(event: WebhookEvent) -> None: ...
# 1.0
from billkit import Event
def handle(event: Event) -> None: ...
```

An empty `secret` now raises `ValueError` rather than failing every delivery,
so a missing environment variable shows up at the first webhook instead of as
a stream of 400s.

**9. `iter()` takes `limit` for the page size**, the name the API and the
other BillKit SDKs use.

```python
# 0.x
client.customers.iter(page_size=100)
# 1.0
client.customers.iter(limit=100)
```

**10. Portal branding clears on `None`.** `tenant.set_portal_branding(logo_url=None)`
used to be dropped; it now removes the logo. Omit a keyword you do not mean
to change.

**11. `httpx_client` and `timeout` together.** If you inject a client and also
pass `timeout=` to `BillKit(...)`, the timeout now applies to every request; it
used to be ignored in favour of the injected client's own. Drop the `timeout=`
to keep the old behaviour.

**12. Renamed fields and list filters.** A keyword the API no longer knows is
a `TypeError` here, and the response keys moved with it:

```python
# 0.x
client.checkout_sessions.create(..., method="ideal")
client.one_shot_payments.create(..., method="ideal")
client.customers.create(email=..., test_clock=clock.id)
dispute["reversed"]; customer["test_clock"]; one_shot["method"]
client.subscriptions.list(status="active,past_due")
# 1.0
client.checkout_sessions.create(..., payment_method="ideal")
client.one_shot_payments.create(..., payment_method="ideal")
client.customers.create(email=..., test_clock_id=clock.id)
dispute.reversed_at; customer.test_clock_id; one_shot.payment_method
client.subscriptions.list(status=["active", "past_due"])   # the string form still works
```

`customers.purge` answers a `PurgedResource`, so read `purged.purged` rather
than fields of the customer.

**13. A missing API key fails at construction, not at the first call.**
`BillKit(api_key="")` (or whitespace, or `None` with `BILLKIT_API_KEY` unset)
raises `ValueError` from the constructor. In 0.x the client was built and
every call answered 401. That moves the failure to wherever you build the
client, which matters when one object both calls the API and verifies
webhooks: built eagerly, a missing key now breaks its construction, and the
webhook route that only ever needed the signing secret fails with it. One
integration answered every delivery with a 500 instead of a 401 that way.
Webhook verification needs no client at all (`WebhookSignature.verify` is
static), so build the client lazily, on first use, or pass `api_key=None` to
fall back to `BILLKIT_API_KEY` rather than `os.environ.get(..., "")`:

```python
# 0.x: built fine with no key, failed per call
client = BillKit(api_key=os.environ.get("BILLKIT_API_KEY", ""))
# 1.0: pass None (or nothing) to read BILLKIT_API_KEY, and build it where it is used
@functools.cache
def billkit_client() -> BillKit:
    return BillKit()
```

## [0.9.0] - 2026-10-09

### Added
- Embedded one-off payments: `one_shot_payments.create(ui_mode="embedded", ...)`, sync and async. Sent without `method`, it creates no provider payment yet and returns a short-lived `client_secret` (with a null `redirect_url`) for the browser payment element in `@billkit-eu/js` / `@billkit-eu/react`, where the payer picks the method. Events, refunds and the payment id work as for a hosted one-shot. **Requires the matching API release.**

### Changed
- `one_shot_payments.create(method=)` is now optional, because an embedded one-shot must omit it. A hosted one-shot still needs it and the API refuses the call without it. The arguments are keyword-only, so no existing call changes.

## [0.8.1] - 2026-09-26

### Changed
- An explicit `None` now also clears `coupons.update(applies_to_price_ids=)` (lifts the price restriction), `coupons.update(min_amount_cents=)` (lifts the minimum), `products.update(marketing_features=)` (empties the list) and `prices.update(refund_window_initial_days=, refund_window_renewal_days=)` (drops the price's override), sync and async. The refund windows were already clearable in the API, but `None` was dropped, so the clear could not be sent from Python. On every other update keyword `None` is still dropped, because the API refuses a null there.
  **Upgrade note:** as in 0.8.0, a null passed through from your own data clears the field: `coupons.update(cid, min_amount_cents=row.min)` with `row.min` sometimes `None` now lifts the coupon's minimum, which widens who can redeem it. Omit the field when you mean "leave it".
  **Requires the matching API release.** An older API accepts the null on `applies_to_price_ids`, `min_amount_cents` and `marketing_features` and leaves the value in place.
- Every other update field refuses an explicit null with a `400` naming the field (the API used to ignore it). The SDK never sends one there; omit the field to leave it unchanged.

## [0.8.0] - 2026-09-26

### Added
- `one_shot_payments.list(customer_id=, status=)` and `one_shot_payments.iter(...)`, sync and async, for `GET /v1/checkout/one_shot`. The filters are carried onto every page.
- `payments.retrieve` documents the `refund_eligibility` expansion: whether a refund of the remaining balance would succeed now, with `window_ends_at` and a `reason` when it would not. Retrieve only; `payments.list` refuses it.

### Changed
- An explicit `None` now clears `products.update(description=)`, `customers.update(name=)`, `webhook_endpoints.update(description=)`, `coupons.update(max_redemptions=, redeem_by=)` and `tax_rates.update(display_name=)`, sent as a JSON null like `default_price_id`. Omitting the keyword still leaves the field alone. Before, `None` on these keywords was dropped, so the clear never reached the API.
  **Upgrade note:** a `None` you pass through from your own data now clears the field. `customers.update(cid, name=user.name)` with `user.name` sometimes `None` used to leave the name alone and now erases it; omit the keyword instead when you mean "leave it".

## [0.7.1] - 2026-09-25

### Added
- `products.update(default_price_id=...)`, sync and async, for the product's default price, the one the billing portal offers on that price's interval. It must be an active price of the same product. Unlike the other keywords on the method, an explicit `default_price_id=None` is sent as a JSON null and clears the default; omitting the keyword leaves it alone.
- `products.retrieve` and `products.list` document the `default_price` expansion alongside `prices` and `stats`.

### Changed
- `tenant.set_billing_profile` documents that the tenant `vat_id` is set once: changing it or clearing it with `None` afterwards raises `InvalidRequestError` (`vat_id_locked` on `vat_id`) and writes nothing, and support changes it. The address fields and `registration_number` still clear with `None`.

## [0.7.0] - 2026-09-23

### Fixed
- Every path id is percent-encoded, so an id containing `/`, `?` or `#` can no longer rewrite the request onto a different route.
- `customers.update(metadata=...)` is typed `dict[str, str] | None`, matching `create`.
- The httpx request-line mitigation is re-checked on the first request, so configuring the `billkit` logger *after* building the client still quiets httpx's full-URL line.

### Added
- `api_keys` resource: `create`, `retrieve`, `revoke`, `list`, `iter`.
- `invoices.send_email(id)` for `POST /v1/invoices/{id}/email`.
- `payments.retrieve_provider(id)` for `GET /v1/payments/{id}/provider`.
- `tenant.billing_profile()` / `tenant.set_billing_profile(...)` for the seller's country, VAT id and invoice address; an explicit `None` clears a field, an omitted keyword leaves it alone.
- `tenant.export()` returns the account's full JSON export as raw bytes.
- `webhook_endpoints.list_event_types()` for the deliverable-event catalogue.
- `expand=[...]` on `customers.list`, `products.list`, `subscriptions.list`, `payments.list`, `invoices.list` and `events.list`, and as a keyword on `products.retrieve`, `subscriptions.retrieve`, `payments.retrieve` and `invoices.retrieve`.
- `payments.list(customer_id=...)`; `invoices.list(customer_id=, subscription_id=, payment_id=, status=)`; `disputes.list(status=, payment_id=)`. All are carried onto every page by `iter()`.
- `checkout_sessions.create(country=...)`, so VAT applies to the first charge on the hosted flow.
- `billing_portal_sessions.create(deliver_email=True)`, which also emails the portal link to the customer.

### Changed
- `prices.update` accepts every field `PriceUpdate` does (`active`, `metadata`, `tax_behavior`, `payment_methods`, `refund_on_cancel`, `refund_window_initial_days`, `refund_window_renewal_days`), all optional, where `active` was previously required and alone.
- `customers.set_vat_number(vat_number=None)` clears the registration: it is sent as an explicit JSON null rather than dropped.
- `coupons.create` documents the two `discount_type` values the API accepts, `"percent"` and `"fixed_cents"`.
- `tenant.set_portal_branding`, `webhook_endpoints.retrieve_delivery` and the `Invoices` class docstrings were corrected; they named a version that never shipped, the node method's old name, and a redirect the transport already follows.

## [0.6.0] - 2026-09-23

### Added
- **`eps` and `paypal` can now start a subscription**, not just take a one-off
  charge. Both are accepted wherever a payment method is named:
  `checkout_sessions.create(method=...)`, a price's `payment_methods`
  allowlist, and `one_shot_payments.create(method=...)`.
  - **EPS** mints a *SEPA* mandate, exactly as iDEAL does, so the subscription
    renews on `directdebit`.
  - EPS is **Austria-only** and carries a **EUR 1.00 minimum**, a hundred
    times iDEAL's. A fully-discounted first charge on a price offering it is
    raised to that floor.
  - **PayPal** mints a `paypal` mandate and renews on itself. No country
    restriction.
  - `bancontact` stays one-off only: Mollie's recurring guide and its
    Bancontact method page disagree about whether it mints a mandate, and that
    is being settled against a live profile rather than guessed.

- **`banktransfer` is accepted by `one_shot_payments.create(method=...)`.**
  One-off only, for the same reason `bancontact` is.
  - It mints no mandate, so it can neither anchor a subscription nor settle a
    metered cycle. Mollie refuses it at `sequenceType=first` with "The payment
    method does not support sequence type", which is why the checkout
    vocabulary stays narrower than the one-shot one.
  - **It settles in days, not seconds.** The payer is handed bank details and
    pays on their own schedule, so Mollie holds the payment `open` for about a
    fortnight and `expires_at` comes back roughly 13 days out.
  - A pending bank transfer is **not a failure and not something to poll**:
    wait for `one_shot_payment.succeeded` / `.failed`.
  - Nothing expires early: the server's reaper keys on the `expires_at` copied
    from Mollie's own answer, not on a BillKit-invented window.
  - Minimum is EUR 0.01, measured against a live profile rather than read off
    a page.

- **`resource_id` filter on `audit_logs.list()` and `audit_logs.iter()`**, on
  both the sync and the async client.
  - It answers "everything that ever happened to this customer", which is the
    question an audit log mostly exists for.
  - The API has always accepted it; this SDK named three of its four filters
    and omitted this one.
  - Matches exactly, and combines with `resource_type` rather than replacing
    it.

## [0.5.0] - 2026-09-22

### Added
- **`invoices.retrieve_pdf(id)` and `credit_notes.retrieve_pdf(id)`** on both
  clients, returning the rendered document as `bytes`. Blob-backed deployments
  stream the bytes inline and S3-backed ones answer `302` to a presigned URL,
  which the transport follows under the SDK's own timeout and retry policy;
  `httpx` drops the `Authorization` header on that cross-origin hop, so the
  API key never reaches the storage host. Node has had this since 0.3.0.

### Fixed
- **The exception class is now chosen by the HTTP status, not the envelope
  `type`.** A request that never reaches a route handler is serialised by the
  API's framework-level handler as `{"type": "api_error"}` *with a 4xx status*,
  so a plain `404` (a typo'd id, an SDK/API version skew) was raised as
  `ServerError`. That told callers BillKit had broken when their own request
  was at fault, and `ServerError` is the class retry and alerting policies key
  on. `type` is still carried verbatim on the raised error. Node already
  behaved this way; python and php now match.
- **`409 idempotency_in_progress` is retried.** It means a request carrying the
  same `Idempotency-Key` is still in flight, so the charge may already have
  happened; surfacing it immediately invited the one workaround that turns a
  single charge into two: retrying with a fresh key. The retry reuses the
  original key, so it either loses the race again or replays the first call's
  result. Every other 409 still fails fast.
- **`BillKit.customers.list()` accepts `provisional`**, the filter its
  `AsyncBillKit` twin has carried since the parameter shipped. The sync client
  silently could not ask for abandoned-checkout rows.

### Changed
- Twenty-eight methods and eleven classes on the sync clients carried a
  one-line docstring, or none, where their async twins explained the call in
  full: `prices.create`, `invoices.void`, `subscriptions.create_usage_record`
  and `retrieve_usage_summary` among them, and eight resource classes that said
  only "Sync flavour of `AsyncX`" and pointed the reader elsewhere. The sync
  client is the one most Python callers reach for; the two halves now document
  themselves identically.

## [0.4.0]

### Added
- **`credit_notes`** on both clients: `retrieve`, `list` and `iter`. A credit
  note is the document that reverses an issued invoice; one is created for you
  when a refund settles, so there is no `create` here. `list` takes
  `invoice_id` to answer "was this sale credited, and by how much".
- **`invoices.void(invoice_id)`** records that an invoice was never owed. It
  keeps its number and stays readable; it just stops being a receivable.

  A **paid** invoice is refused with a `ConflictError` whose `code` is
  `"invoice_not_voidable"`. Once the money has moved, "never owed" is not
  true; refund the payment instead, and a credit note is issued when the
  refund settles. Voiding twice is a no-op.

  There is no `retrieve_pdf`: this client has no binary response path yet, so
  invoice PDFs are not fetchable either. Use the URL from the API directly.

## [0.3.0]

### Added
- **Metered pricing below one minor unit.** `prices.create(...)` takes
  `unit_amount_decimal`: a per-unit rate in **minor units** with up to 12
  decimal places, so "€0.0002 per API call" (`"0.02"`, i.e. 0.02 cents) is
  finally expressible. `amount_cents` is an integer and could never say it.
  Metered prices only.

  It is sent **as a string**, and it is accepted as `str`, `int` or `Decimal`.
  A `float` raises `TypeError` instead of being coerced: a float cannot hold
  0.0002 exactly, so coercing would work for the values that happen to
  round-trip and silently mis-price the ones that do not. A `Decimal` is
  formatted with `f`, never `str()`, because `str(Decimal("1E-12"))` is
  `"1E-12"` and the API refuses exponent notation, and an echoed
  `"0.000000000001"` would not be the string you sent.
- **Tiered pricing.** `prices.create(billing_scheme="tiered", tiers_mode=...,
  tiers=[...])`. `tiers_mode="graduated"` prices the units inside each band;
  `"volume"` lets the period total pick one band which then prices every unit.
  The same table under the two modes is a different bill, so the mode is
  required rather than defaulted. Each band's `unit_amount_decimal` gets the
  same float guard, and the tier list is copied rather than rewritten in place
  (a price definition is usually a module constant).
- **`identifier` on `subscriptions.create_usage_record(...)`**, for the retry
  an `Idempotency-Key` cannot catch. The key covers a retry of one HTTP
  request; `identifier` covers a retry of *your own* call (a job runner
  replaying a task, a queue delivering twice), which arrives as a genuinely
  new request with a new key. It is unique within the subscription, and a
  second report of the same identifier returns the first record unchanged
  rather than billing twice. If your pipeline is at-least-once, this is the
  one that matters.
- **`subscriptions.retrieve_usage_summary(subscription_id)`**, the money view
  of pending usage: `pending_quantity`, `net_cents` / `tax_cents` /
  `gross_cents` computed through the same rate or tier table the period close
  uses, and `will_charge`. Read `will_charge` before promising a customer an
  amount: a period under `minimum_charge_cents` (€1.00) is **not** charged,
  because the provider would refuse it, and the usage rolls into the next
  period instead. Previously the only record of that decision was a server log
  line. `open_invoice_id` names an earlier cycle still unsettled.
- **`refund_on_cancel` on `prices.create(...)`.** Server-side since the
  `0066` migration and unreachable from this SDK until now. `"full"` or
  `"prorated"` issues the refund a cancellation promised without anyone having
  to remember to. Metered prices must leave it at `"none"`.

### Changed
- `prices.create(amount_cents=...)` is now keyword-**optional**, because a
  price can be priced by `unit_amount_decimal` or by `tiers` instead. Exactly
  one of the three is required, and the server refuses a price with none of
  them (`parameter_missing`). Existing calls are unaffected.

## [0.2.1]

### Changed
- Documentation only. API keys are now `bk_live_…` / `bk_test_…` and webhook
  signing secrets `bkwhsec_…`; every example here used the previous
  Stripe-shaped `sk_`/`whsec_` spelling. No code in this package changed: it
  never parsed the prefix, it forwards the key as a bearer token.

## [0.2.0]

### Added
- `client.prices.update(price_id, active=False)` (and `AsyncPrices.update`)
  archives a price through `POST /v1/prices/{id}`. The price keeps its id and
  stays readable through `retrieve()` and `list()`, because subscriptions renew
  against it by id. Subscriptions already on it keep renewing; what stops is new
  business. Re-archiving is a no-op that returns the price unchanged, so a retry
  is safe. `active` is the only field a price accepts and `active=True` is
  refused, because prices are immutable.
- `subscriptions.list()` and `.iter()` take `customer_id`, `status` and
  `renewal_state`, on both the sync and async clients. `iter()` carries the
  filters onto every page request rather than filtering a walk locally. Both
  `status` and `renewal_state` take a comma-separated list.

### Removed
- `delete()` on `products`, `prices`, `coupons`, `tax_rates` and
  `webhook_endpoints`, on both the sync and async clients. None of them deleted
  anything: every one of those rows stays readable afterwards, which is why they
  have to. Retire them through the update route instead: `active=False` for
  products, prices, tax rates and coupons, `status="disabled"` for webhook
  endpoints. The server no longer answers `DELETE` on those paths at all.

### Changed
- `client.customers.delete(customer_id)` returns
  `{"id": ..., "object": "customer", "deleted": True}` instead of the customer.
  The customer leaves the API, so returning a body that reads like a live
  resource said the opposite of what happened.
- Paused subscriptions are found with `renewal_state="paused"`.
  `status="paused"` is no longer accepted by the API and raises
  `InvalidRequestError`: pausing sets `renewal_state` and leaves `status` at
  `active`, because the customer has paid for the period they are in. The README
  documents the split between the two filters.

## [0.1.1]

### Fixed
- The README's alternate install line said `uv add billkit`, which installs an
  unrelated project that happens to hold the bare name on PyPI. It now reads
  `uv add billkit-eu`, matching the `pip install` line directly above it, and
  the distinction between the distribution name (`billkit-eu`) and the import
  name (`billkit`) is spelled out rather than left to be inferred.

  No code changed. This is a documentation-only release, but the documentation
  in question is the install instruction shown on the PyPI page, so following it
  got you the wrong package.

## [0.1.0]

First public release.

### Added
- Sync (`BillKit`) and async (`AsyncBillKit`) clients over `httpx`.
- Full resource coverage: Customers, Products, Prices, CheckoutSessions,
  Subscriptions, Refunds, WebhookEndpoints, Coupons, TaxRates, Invoices,
  AuditLogs, Payments, Events, Tenant, BillingPortalSessions.
- Cursor pagination with `.list()` and `.iter()` helpers.
- Typed exception hierarchy (`APIConnectionError`, `AuthenticationError`,
  `PermissionError`, `ResourceMissingError`, `InvalidRequestError`,
  `ConflictError`, `RateLimitError`, `ServerError`) matching the BillKit
  error envelope (`code`, `reason`, `message`, `param`).
- Automatic idempotency-key generation on every mutating call; overridable
  via `idempotency_key=...` kwarg.
- Retry policy: 4 attempts, jittered exponential backoff (0.5→1→2→4s,
  capped at 8s), retries network errors + 5xx (with idempotency) and 429
  (respecting `Retry-After`).
- Configurable timeout (default 30s).
- `WebhookSignature.verify(payload, signature_header, secret)` for
  verifying `BillKit-Signature` webhooks (HMAC-SHA256 with 5-min replay
  protection). Multiple `v1` signatures in one header are accepted and pass if
  any matches, which is what keeps inbound webhooks verifying through a
  signing-secret rotation.
- **Opt-in logging.** The SDK emits its request/retry lifecycle to a single
  `logging.getLogger("billkit")` with a `NullHandler` attached, the standard
  library-author pattern. It stays silent until *your* application raises the
  level, and it never calls `basicConfig`, never sets a level, and never adds a
  handler to a logger it doesn't own.

  ```python
  logging.getLogger("billkit").setLevel(logging.DEBUG)
  ```

  DEBUG gets one line per HTTP attempt and one per response (status, elapsed ms,
  `X-Request-Id`); WARNING gets one line per retry. The logger is exported as
  `billkit.logger`.

  API keys, request/response bodies and query strings are never logged, and the
  final failure is raised rather than logged so you never get a duplicate entry.

[Unreleased]: https://github.com/billkit-eu/billkit-python/compare/v1.1.1...HEAD
[1.1.1]: https://github.com/billkit-eu/billkit-python/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/billkit-eu/billkit-python/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/billkit-eu/billkit-python/compare/v0.9.0...v1.0.0
[0.9.0]: https://github.com/billkit-eu/billkit-python/compare/v0.8.1...v0.9.0
[0.8.1]: https://github.com/billkit-eu/billkit-python/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/billkit-eu/billkit-python/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/billkit-eu/billkit-python/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/billkit-eu/billkit-python/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/billkit-eu/billkit-python/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/billkit-eu/billkit-python/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/billkit-eu/billkit-python/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/billkit-eu/billkit-python/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/billkit-eu/billkit-python/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/billkit-eu/billkit-python/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/billkit-eu/billkit-python/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/billkit-eu/billkit-python/releases/tag/v0.1.0
