"""Top-level client classes: :class:`BillKit` (sync) +
:class:`AsyncBillKit` (async).

Both share the same surface, so pick whichever fits your runtime.
"""

from __future__ import annotations

import os
from types import TracebackType
from typing import Self

import httpx

from billkit._options import NOT_GIVEN, NotGiven
from billkit._retry import RetryPolicy
from billkit._transport import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    AsyncTransport,
    SyncTransport,
)
from billkit.resources import (
    ApiKeys,
    AsyncApiKeys,
    AsyncAuditLogs,
    AsyncBillingPortalSessions,
    AsyncCheckoutSessions,
    AsyncCoupons,
    AsyncCreditNotes,
    AsyncCustomers,
    AsyncDisputes,
    AsyncEvents,
    AsyncInvoices,
    AsyncOneShotPayments,
    AsyncPayments,
    AsyncPrices,
    AsyncProducts,
    AsyncRefunds,
    AsyncSubscriptions,
    AsyncTaxRates,
    AsyncTenant,
    AsyncTestClocks,
    AsyncWebhookEndpoints,
    AuditLogs,
    BillingPortalSessions,
    CheckoutSessions,
    Coupons,
    CreditNotes,
    Customers,
    Disputes,
    Events,
    Invoices,
    OneShotPayments,
    Payments,
    Prices,
    Products,
    Refunds,
    Subscriptions,
    TaxRates,
    Tenant,
    TestClocks,
    WebhookEndpoints,
)


def _resolve_api_key(supplied: str | None) -> str:
    # ``None`` means "not passed" and falls back to the environment. An
    # explicit empty key is a caller bug (an unset variable read as ``""``),
    # refused here rather than sent as ``Authorization: Bearer `` and
    # answered with a 401 on the first call.
    if supplied is not None:
        if not supplied.strip():
            raise ValueError(
                "BillKit API key is empty. Pass a bk_live_/bk_test_ key, or leave "
                "api_key unset to read BILLKIT_API_KEY."
            )
        return supplied
    env = os.environ.get("BILLKIT_API_KEY")
    if env:
        return env
    raise ValueError("No BillKit API key supplied. Pass api_key=... or set BILLKIT_API_KEY.")


class AsyncBillKit:
    """Async client for the BillKit API.

    ``timeout`` is seconds or an ``httpx.Timeout`` (default: 30s, 10s to
    connect), applied to every request, including through an injected
    ``httpx_client``; leave it out to keep an injected client's own default.
    ``retry_policy`` is a :class:`billkit.RetryPolicy`. ``httpx_client`` is
    an ``httpx.AsyncClient`` you own (proxies, custom transports, tests);
    the SDK does not close it. ``httpx`` is part of this SDK's public
    surface for exactly these three arguments.

    Use as an async context manager to ensure the underlying
    httpx client is closed::

        async with AsyncBillKit(api_key="bk_test_...") as client:
            customer = await client.customers.create(email="...")

    Or instantiate directly and call :meth:`close` when done.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: httpx.Timeout | float | NotGiven | None = NOT_GIVEN,
        retry_policy: RetryPolicy | None = None,
        httpx_client: httpx.AsyncClient | None = None,
    ) -> None:
        resolved_timeout = _normalise_timeout(timeout, injected=httpx_client is not None)
        self._transport = AsyncTransport(
            api_key=_resolve_api_key(api_key),
            base_url=base_url,
            timeout=resolved_timeout,
            retry_policy=retry_policy,
            httpx_client=httpx_client,
        )
        self.api_keys = AsyncApiKeys(self._transport)
        self.customers = AsyncCustomers(self._transport)
        self.products = AsyncProducts(self._transport)
        self.prices = AsyncPrices(self._transport)
        self.checkout_sessions = AsyncCheckoutSessions(self._transport)
        self.one_shot_payments = AsyncOneShotPayments(self._transport)
        self.subscriptions = AsyncSubscriptions(self._transport)
        self.refunds = AsyncRefunds(self._transport)
        self.disputes = AsyncDisputes(self._transport)
        self.webhook_endpoints = AsyncWebhookEndpoints(self._transport)
        self.events = AsyncEvents(self._transport)
        self.tenant = AsyncTenant(self._transport)
        self.coupons = AsyncCoupons(self._transport)
        self.tax_rates = AsyncTaxRates(self._transport)
        self.invoices = AsyncInvoices(self._transport)
        self.credit_notes = AsyncCreditNotes(self._transport)
        self.audit_logs = AsyncAuditLogs(self._transport)
        self.payments = AsyncPayments(self._transport)
        self.billing_portal_sessions = AsyncBillingPortalSessions(self._transport)
        self.test_clocks = AsyncTestClocks(self._transport)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._transport.close()


class BillKit:
    """Sync client for the BillKit API.

    Use as a context manager or call :meth:`close` when done::

        with BillKit(api_key="bk_test_...") as client:
            customer = client.customers.create(email="...")
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: httpx.Timeout | float | NotGiven | None = NOT_GIVEN,
        retry_policy: RetryPolicy | None = None,
        httpx_client: httpx.Client | None = None,
    ) -> None:
        resolved_timeout = _normalise_timeout(timeout, injected=httpx_client is not None)
        self._transport = SyncTransport(
            api_key=_resolve_api_key(api_key),
            base_url=base_url,
            timeout=resolved_timeout,
            retry_policy=retry_policy,
            httpx_client=httpx_client,
        )
        self.api_keys = ApiKeys(self._transport)
        self.customers = Customers(self._transport)
        self.products = Products(self._transport)
        self.prices = Prices(self._transport)
        self.checkout_sessions = CheckoutSessions(self._transport)
        self.one_shot_payments = OneShotPayments(self._transport)
        self.subscriptions = Subscriptions(self._transport)
        self.refunds = Refunds(self._transport)
        self.disputes = Disputes(self._transport)
        self.webhook_endpoints = WebhookEndpoints(self._transport)
        self.events = Events(self._transport)
        self.tenant = Tenant(self._transport)
        self.coupons = Coupons(self._transport)
        self.tax_rates = TaxRates(self._transport)
        self.invoices = Invoices(self._transport)
        self.credit_notes = CreditNotes(self._transport)
        self.audit_logs = AuditLogs(self._transport)
        self.payments = Payments(self._transport)
        self.billing_portal_sessions = BillingPortalSessions(self._transport)
        self.test_clocks = TestClocks(self._transport)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._transport.close()


def _normalise_timeout(
    value: httpx.Timeout | float | NotGiven | None, *, injected: bool
) -> httpx.Timeout | NotGiven | None:
    """The timeout the transport sends with every request.

    Passed explicitly, it applies to every request whoever built the
    ``httpx`` client: 0.x set it only on a client it created, so with an
    injected ``httpx_client`` it was silently ignored. Not passed, an
    injected client keeps its own configured default (``NOT_GIVEN`` here),
    and a client the SDK creates gets :data:`DEFAULT_TIMEOUT`. ``None``
    disables the timeout.
    """
    if isinstance(value, NotGiven):
        return value if injected else DEFAULT_TIMEOUT
    if value is None or isinstance(value, httpx.Timeout):
        return value
    return httpx.Timeout(value)
