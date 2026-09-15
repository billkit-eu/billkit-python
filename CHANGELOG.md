# Changelog

All notable changes to the BillKit Python SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning is independent of the Node SDK; the two ship on their own cadence,
so the numbers will diverge after this first release.

Published to PyPI as `billkit-eu`; the import name is `billkit`.

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
