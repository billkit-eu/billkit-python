# Changelog

All notable changes to the BillKit Python SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning is independent of the Node SDK; the two ship on their own cadence,
so the numbers will diverge after this first release.

Published to PyPI as `billkit-eu`; the import name is `billkit`.

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
  so a plain `404` — a typo'd id, an SDK/API version skew — was raised as
  `ServerError`. That told callers BillKit had broken when their own request
  was at fault, and `ServerError` is the class retry and alerting policies key
  on. `type` is still carried verbatim on the raised error. Node already
  behaved this way; python and php now match.
- **`409 idempotency_in_progress` is retried.** It means a request carrying the
  same `Idempotency-Key` is still in flight, so the charge may already have
  happened; surfacing it immediately invited the one workaround that turns a
  single charge into two — retrying with a fresh key. The retry reuses the
  original key, so it either loses the race again or replays the first call's
  result. Every other 409 still fails fast.
- **`BillKit.customers.list()` accepts `provisional`**, the filter its
  `AsyncBillKit` twin has carried since the parameter shipped. The sync client
  silently could not ask for abandoned-checkout rows.

### Changed
- Twenty-eight methods and eleven classes on the sync clients carried a
  one-line docstring, or none, where their async twins explained the call in
  full — `prices.create`, `invoices.void`, `subscriptions.create_usage_record`
  and `retrieve_usage_summary` among them, and eight resource classes that said
  only "Sync flavour of `AsyncX`" and pointed the reader elsewhere. The sync
  client is the one most Python callers reach for; the two halves now document
  themselves identically.

## [0.4.0]

### Added
- **`credit_notes`** on both clients — `retrieve`, `list` and `iter`. A credit
  note is the document that reverses an issued invoice; one is created for you
  when a refund settles, so there is no `create` here. `list` takes
  `invoice_id` to answer "was this sale credited, and by how much".
- **`invoices.void(invoice_id)`** — records that an invoice was never owed. It
  keeps its number and stays readable; it just stops being a receivable.

  A **paid** invoice is refused with a `ConflictError` whose `code` is
  `"invoice_not_voidable"`. Once the money has moved, "never owed" is not
  true — refund the payment instead, and a credit note is issued when the
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
  `"1E-12"` and the API refuses exponent notation — an echoed
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
  request; `identifier` covers a retry of *your own* call — a job runner
  replaying a task, a queue delivering twice — which arrives as a genuinely
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
  have to. Retire them through the update route instead — `active=False` for
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

[Unreleased]: https://github.com/billkit-eu/billkit-python/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/billkit-eu/billkit-python/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/billkit-eu/billkit-python/releases/tag/v0.1.0
