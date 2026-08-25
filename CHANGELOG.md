# Changelog

All notable changes to the BillKit Python SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning is independent of the Node SDK; the two ship on their own cadence,
so the numbers will diverge after this first release.

Published to PyPI as `billkit-eu`; the import name is `billkit`.

## [Unreleased]

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

[Unreleased]: https://github.com/billkit-eu/billkit-python/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/billkit-eu/billkit-python/releases/tag/v0.1.0
