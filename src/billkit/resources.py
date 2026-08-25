"""Resource accessors mirroring the BillKit API surface.

Each resource exposes the public verbs from ``/v1/<resource>``. The
return type is ``dict[str, Any]``; the SDK doesn't ship Pydantic
models for responses because the API is Stripe-shape and tenants
typically forward the JSON through to their own data layer
verbatim. Callers who want strong types can wrap the return value
in their own Pydantic models or :class:`typing.TypedDict`.

Each class has an ``Async*`` and a sync flavor. They share the
same method signatures so code reading either side feels the same.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any, Protocol

from billkit._pagination import aiterate, paginate


class _AsyncRequester(Protocol):
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = ...,
        json_body: dict[str, Any] | None = ...,
        idempotency_key: str | None = ...,
        extra_headers: dict[str, str] | None = ...,
    ) -> dict[str, Any]: ...


class _SyncRequester(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = ...,
        json_body: dict[str, Any] | None = ...,
        idempotency_key: str | None = ...,
        extra_headers: dict[str, str] | None = ...,
    ) -> dict[str, Any]: ...


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _list_params(
    *, limit: int | None, starting_after: str | None, ending_before: str | None
) -> dict[str, Any]:
    return _drop_none(
        {"limit": limit, "starting_after": starting_after, "ending_before": ending_before}
    )


# ─── Async resources ───────────────────────────────────────────────


class AsyncCustomers:
    """Create, update, delete, and page through BillKit customers.

    Customers are tenant-scoped buyer records. Use them as the anchor
    for checkout sessions, subscriptions, invoices, refunds, and audit
    history. Methods return the API's raw JSON dictionaries so callers
    can preserve fields added by newer API versions.
    """

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def create(
        self,
        *,
        email: str | None = None,
        name: str | None = None,
        country_code: str | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "email": email,
                "name": name,
                "country_code": country_code,
                "metadata": metadata,
            }
        )
        return await self._t.request(
            "POST", "/v1/customers", json_body=body, idempotency_key=idempotency_key
        )

    async def set_vat_number(
        self,
        customer_id: str,
        *,
        vat_number: str,
        country_code: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Attach or replace the customer's VAT number.

        Triggers server-side VIES validation. The response carries the
        updated ``vat_number`` plus ``vat_number_validated``; a ``False``
        flag means VIES is reachable but the number didn't validate, or
        the validation is still pending.
        """
        body = _drop_none({"vat_number": vat_number, "country_code": country_code})
        return await self._t.request(
            "POST",
            f"/v1/customers/{customer_id}/vat_number",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def purge(
        self,
        customer_id: str,
        *,
        confirmed: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Hard-purge a customer's PII for GDPR erasure requests.

        Distinct from :meth:`delete` (soft delete): purge nulls email,
        name, country, VAT, and metadata, sets ``purged_at``, and is
        irreversible. ``confirmed=False`` no-ops at the API as a
        fat-finger guard, so the SDK defaults it to ``True``.
        """
        return await self._t.request(
            "POST",
            f"/v1/customers/{customer_id}/purge",
            json_body={"confirmed": confirmed},
            idempotency_key=idempotency_key,
        )

    async def retrieve(self, customer_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/customers/{customer_id}")

    async def update(
        self,
        customer_id: str,
        *,
        email: str | None = None,
        name: str | None = None,
        country_code: str | None = None,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "email": email,
                "name": name,
                "country_code": country_code,
                "metadata": metadata,
            }
        )
        return await self._t.request(
            "POST",
            f"/v1/customers/{customer_id}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def delete(
        self, customer_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._t.request(
            "DELETE", f"/v1/customers/{customer_id}", idempotency_key=idempotency_key
        )

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "GET",
            "/v1/customers",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each customer.

        Page size defaults to the server's default (10). Pass
        ``page_size=100`` to reduce round-trips on large tenant data.
        """
        return aiterate(self.list, page_size=page_size)


class AsyncProducts:
    """Manage catalog products.

    A Product is the customer-facing thing being sold (for example,
    ``"Pro"`` or ``"Enterprise"``). Create one Product, then attach one
    or more Prices to it for currencies, billing intervals, trials, or
    payment-method mixes.
    """

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def create(
        self,
        *,
        name: str,
        description: str | None = None,
        marketing_features: list[str] | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a catalog product and return the product object."""
        body = _drop_none(
            {
                "name": name,
                "description": description,
                "marketing_features": marketing_features,
                "metadata": metadata,
            }
        )
        return await self._t.request(
            "POST", "/v1/products", json_body=body, idempotency_key=idempotency_key
        )

    async def retrieve(self, product_id: str) -> dict[str, Any]:
        """Fetch one product by id."""
        return await self._t.request("GET", f"/v1/products/{product_id}")

    async def update(
        self,
        product_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        marketing_features: list[str] | None = None,
        metadata: dict[str, str] | None = None,
        active: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Patch mutable product fields.

        Pass only the fields you want to change. Use ``active=False``
        to stop selling a product without deleting historical data.
        """
        body = _drop_none(
            {
                "name": name,
                "description": description,
                "marketing_features": marketing_features,
                "metadata": metadata,
                "active": active,
            }
        )
        return await self._t.request(
            "POST",
            f"/v1/products/{product_id}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def delete(
        self, product_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Archive a product and return its final representation."""
        return await self._t.request(
            "DELETE", f"/v1/products/{product_id}", idempotency_key=idempotency_key
        )

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        """List products in reverse creation order."""
        return await self._t.request(
            "GET",
            "/v1/products",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each product."""
        return aiterate(self.list, page_size=page_size)


class AsyncPrices:
    """Manage immutable billing prices attached to products."""

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def create(
        self,
        *,
        product_id: str,
        amount_cents: int,
        currency: str,
        interval: str,
        metadata: dict[str, str] | None = None,
        trial_days: int | None = None,
        trial_verification_cents: int | None = None,
        payment_methods: list[str] | None = None,
        refund_window_initial_days: int | None = None,
        refund_window_renewal_days: int | None = None,
        tax_behavior: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a price for an existing product.

        Prices are append-only billing terms. Create a new Price when
        changing amount, interval, trial, or supported payment methods;
        existing subscriptions keep pointing at their original Price.

        ``refund_window_initial_days`` and ``refund_window_renewal_days``
        override the default refund policy (7d / 30d initial, 3d
        renewal) on a per-price basis. Leave ``None`` to inherit the
        default; pass ``0`` to disable refunds for that charge type;
        pass ``N > 0`` for an ``N``-day window (capped server-side at
        365). Useful for "Pro Bundle has a 14-day money-back guarantee"
        or "Lifetime plan has no refunds" product decisions.

        ``tax_behavior`` says whether ``amount_cents`` is quoted gross
        (``"inclusive"``, VAT is backed out of it) or net
        (``"exclusive"``, VAT is added on top at charge time). Leave
        ``None`` to inherit ``"unspecified"``, which defers to the tax
        rate configured for the buyer's country. Set it explicitly when
        the amount you advertise has to be the amount charged regardless
        of what tax rates exist now or later.
        """
        body = _drop_none(
            {
                "product_id": product_id,
                "amount_cents": amount_cents,
                "currency": currency,
                "interval": interval,
                "metadata": metadata,
                "trial_days": trial_days,
                "trial_verification_cents": trial_verification_cents,
                "payment_methods": payment_methods,
                "refund_window_initial_days": refund_window_initial_days,
                "refund_window_renewal_days": refund_window_renewal_days,
                "tax_behavior": tax_behavior,
            }
        )
        return await self._t.request(
            "POST", "/v1/prices", json_body=body, idempotency_key=idempotency_key
        )

    async def retrieve(self, price_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/prices/{price_id}")

    async def list(
        self,
        *,
        product_id: str | None = None,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        """List prices, optionally narrowed to one product.

        ``product_id`` is applied server-side (``GET
        /v1/prices?product_id=...``), which beats listing everything and
        filtering client-side once a tenant has more than a page of prices.
        """
        params = _list_params(
            limit=limit, starting_after=starting_after, ending_before=ending_before
        )
        if product_id is not None:
            params["product_id"] = product_id
        return await self._t.request("GET", "/v1/prices", params=params)

    def iter(
        self, *, product_id: str | None = None, page_size: int | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each price."""

        async def _bound(**kwargs: Any) -> dict[str, Any]:
            return await self.list(product_id=product_id, **kwargs)

        return aiterate(_bound, page_size=page_size)


class AsyncCheckoutSessions:
    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def create(
        self,
        *,
        customer_id: str | None = None,
        customer_email: str | None = None,
        customer_name: str | None = None,
        price_id: str,
        success_url: str,
        cancel_url: str,
        method: str | None = None,
        coupon_code: str | None = None,
        trial_days_override: int | None = None,
        ui_mode: str | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a hosted checkout session.

        Pass **exactly one of** ``customer_id`` (existing Customer) or
        ``customer_email`` (Stripe-compat shortcut: BillKit creates a
        fresh Customer in the same transaction, and never dedupes by
        email). ``customer_name`` is only valid with ``customer_email``
        and is carried onto the auto-created Customer row; rename an
        existing customer via ``customers.update`` instead.

        ``method`` pins the Mollie payment method (``"creditcard"`` or
        ``"directdebit"``); ``None`` lets Mollie pick. ``coupon_code`` is
        atomically claimed at session creation. ``trial_days_override``
        replaces the price's trial for this session only and is server
        capped at ``2 * max(price.trial_days, 14)`` (``0`` disables a
        trial that the price would otherwise grant).

        ``metadata`` is an opaque key/value bag BillKit stores verbatim
        and echoes back on the session and on the
        ``checkout.session.completed`` webhook. Use it to carry your own
        record id through checkout. BillKit never dedupes customers by
        email, so ``customer_id`` alone is ambiguous once the same buyer
        checks out twice (resubscribe, plan change).

        ``ui_mode`` selects the payment surface:

        * ``None`` / ``"hosted"`` (default): the response's ``url``
          points at **Mollie's hosted checkout page**
          (``https://www.mollie.com/checkout/...``). Redirect the buyer
          there; Mollie collects card details under the merchant's
          Mollie profile branding, then redirects back to ``success_url``
          (or ``cancel_url``).
        * ``"embedded"``: no charge is created yet. The response carries
          a short-lived ``client_secret`` (``url`` stays ``None``) for
          ``<CheckoutElement/>`` from ``@billkit-eu/js`` / ``@billkit-eu/react``
          to mount against, so the card form renders in your own page.
          ``method`` must be omitted; it is chosen inside the element.

        Either way tenant branding kicks in once the buyer reaches the
        post-purchase portal via ``billing_portal_sessions.create``.
        """
        body = _drop_none(
            {
                "customer_id": customer_id,
                "customer_email": customer_email,
                "customer_name": customer_name,
                "price_id": price_id,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "method": method,
                "coupon_code": coupon_code,
                "trial_days_override": trial_days_override,
                "ui_mode": ui_mode,
                "metadata": metadata,
            }
        )
        return await self._t.request(
            "POST",
            "/v1/checkout/sessions",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def retrieve(self, session_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/checkout/sessions/{session_id}")


class AsyncOneShotPayments:
    """Mandate-less one-shot payments (``/v1/checkout/one_shot``).

    A one-shot is the Stripe PaymentIntent shape mapped onto Mollie: a
    single ``sequenceType=oneoff`` charge that provisions nothing: no
    subscription, no mandate, no renewals. Drive terminal state via the
    ``one_shot_payment.succeeded`` / ``.failed`` webhook events; refund
    one with ``refunds.create(one_shot_payment_id=...)``.
    """

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def create(
        self,
        *,
        customer_id: str,
        amount_cents: int,
        currency: str,
        method: str,
        success_url: str,
        cancel_url: str | None = None,
        description: str | None = None,
        refund_window_days: int | None = None,
        tax_behavior: str | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a one-off charge.

        ``method`` is required and validated against the tenant's Mollie
        capability allowlist for ``currency`` (one-off-only methods like
        ``bancontact`` / ``eps`` are allowed here even though they can't
        back a subscription). ``refund_window_days`` overrides
        the one-shot default (30d) for this payment: ``None`` inherits the
        default, ``0`` disables refunds, ``N > 0`` is an ``N``-day window
        (values above 365 are rejected server-side).

        ``tax_behavior`` says whether ``amount_cents`` is quoted gross or
        net. ``"inclusive"`` (the default when omitted) charges it as-is
        and backs the VAT out; ``"exclusive"`` reads it as a net figure
        and charges ``amount_cents + tax``, so the response's
        ``amount_cents`` comes back *larger* than the one you sent; it is
        always what was actually charged. Reconcile against ``net_cents``
        / ``tax_cents`` on the response. ``None`` inherits the country
        default from your configured tax rate.

        The response's ``redirect_url`` points at Mollie's hosted checkout.
        Redirect the payer there; terminal state arrives via the
        ``one_shot_payment.*`` webhook events.
        """
        body = _drop_none(
            {
                "customer_id": customer_id,
                "amount_cents": amount_cents,
                "currency": currency,
                "method": method,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "description": description,
                "refund_window_days": refund_window_days,
                "tax_behavior": tax_behavior,
                "metadata": metadata,
            }
        )
        return await self._t.request(
            "POST", "/v1/checkout/one_shot", json_body=body, idempotency_key=idempotency_key
        )

    async def retrieve(self, one_shot_payment_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/checkout/one_shot/{one_shot_payment_id}")


class AsyncSubscriptions:
    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def retrieve(self, subscription_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/subscriptions/{subscription_id}")

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "GET",
            "/v1/subscriptions",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each subscription."""
        return aiterate(self.list, page_size=page_size)

    async def cancel(
        self, subscription_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._t.request(
            "POST",
            f"/v1/subscriptions/{subscription_id}/cancel",
            idempotency_key=idempotency_key,
        )

    async def pause(
        self, subscription_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._t.request(
            "POST",
            f"/v1/subscriptions/{subscription_id}/pause",
            idempotency_key=idempotency_key,
        )

    async def resume(
        self, subscription_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._t.request(
            "POST",
            f"/v1/subscriptions/{subscription_id}/resume",
            idempotency_key=idempotency_key,
        )

    async def reactivate(
        self, subscription_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Reactivate a subscription that's been canceled but is still
        inside its paid-through period.

        Distinct from :meth:`resume` (paused → active): reactivate
        flips ``canceled`` back to ``active`` for the remainder of the
        current period, so the customer keeps service without a new
        checkout. Returns 409 if the period has already elapsed.
        """
        return await self._t.request(
            "POST",
            f"/v1/subscriptions/{subscription_id}/reactivate",
            idempotency_key=idempotency_key,
        )

    async def preview_update(
        self, subscription_id: str, *, target_price_id: str
    ) -> dict[str, Any]:
        return await self._t.request(
            "POST",
            f"/v1/subscriptions/{subscription_id}/preview_update",
            json_body={"target_price_id": target_price_id},
        )

    async def update(
        self,
        subscription_id: str,
        *,
        target_price_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "POST",
            f"/v1/subscriptions/{subscription_id}/update",
            json_body={"target_price_id": target_price_id},
            idempotency_key=idempotency_key,
        )

    async def reauthorize_payment_method(
        self,
        subscription_id: str,
        *,
        return_url: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "POST",
            f"/v1/subscriptions/{subscription_id}/reauthorize_payment_method",
            json_body={"return_url": return_url},
            idempotency_key=idempotency_key,
        )


class AsyncRefunds:
    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def create(
        self,
        *,
        payment_id: str | None = None,
        subscription_id: str | None = None,
        one_shot_payment_id: str | None = None,
        amount_cents: int | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        # Pass exactly one target: ``payment_id`` / ``subscription_id`` for a
        # subscription-bound charge, or ``one_shot_payment_id`` for a
        # mandate-less one-shot. The server rejects an ambiguous combination.
        # ``amount_cents`` is optional: omit it to refund the whole remaining
        # balance, or pass a smaller amount for a partial refund (a payment may
        # carry several partials up to the charged amount).
        body = _drop_none(
            {
                "payment_id": payment_id,
                "subscription_id": subscription_id,
                "one_shot_payment_id": one_shot_payment_id,
                "amount_cents": amount_cents,
                "reason": reason,
            }
        )
        return await self._t.request(
            "POST", "/v1/refunds", json_body=body, idempotency_key=idempotency_key
        )

    async def retrieve(self, refund_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/refunds/{refund_id}")

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "GET",
            "/v1/refunds",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each refund."""
        return aiterate(self.list, page_size=page_size)


class AsyncDisputes:
    """Chargebacks / disputes. Read-only.

    Disputes are provider-originated (opened by the cardholder's bank) and
    surfaced via the ``dispute.created`` / ``dispute.closed`` webhook events.
    There is no create/update. A dispute's ``status`` is ``open`` or ``won``
    (chargeback reversed); Mollie exposes no "lost" signal, so an upheld
    chargeback stays ``open`` (treat any non-``won`` dispute as unresolved).
    """

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def retrieve(self, dispute_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/disputes/{dispute_id}")

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "GET",
            "/v1/disputes",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each dispute."""
        return aiterate(self.list, page_size=page_size)


class AsyncWebhookEndpoints:
    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def create(
        self,
        *,
        url: str,
        enabled_events: list[str] | None = None,
        description: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {"url": url, "enabled_events": enabled_events, "description": description}
        )
        return await self._t.request(
            "POST",
            "/v1/webhook_endpoints",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def retrieve(self, endpoint_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/webhook_endpoints/{endpoint_id}")

    async def update(
        self,
        endpoint_id: str,
        *,
        url: str | None = None,
        enabled_events: list[str] | None = None,
        description: str | None = None,
        status: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "url": url,
                "enabled_events": enabled_events,
                "description": description,
                "status": status,
            }
        )
        return await self._t.request(
            "POST",
            f"/v1/webhook_endpoints/{endpoint_id}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def delete(
        self, endpoint_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._t.request(
            "DELETE",
            f"/v1/webhook_endpoints/{endpoint_id}",
            idempotency_key=idempotency_key,
        )

    async def rotate_secret(
        self, endpoint_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._t.request(
            "POST",
            f"/v1/webhook_endpoints/{endpoint_id}/rotate_secret",
            idempotency_key=idempotency_key,
        )

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "GET",
            "/v1/webhook_endpoints",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each endpoint."""
        return aiterate(self.list, page_size=page_size)

    async def list_deliveries(
        self,
        endpoint_id: str,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        """List per-attempt delivery records for one endpoint.

        Useful when a tenant's receiver is failing. Surfaces the
        status code, response body excerpt, error, and next-attempt
        timestamp for each event x endpoint pair.
        """
        return await self._t.request(
            "GET",
            f"/v1/webhook_endpoints/{endpoint_id}/deliveries",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter_deliveries(
        self, endpoint_id: str, *, page_size: int | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list_deliveries`` for one endpoint."""

        async def _bound(**kwargs: Any) -> dict[str, Any]:
            return await self.list_deliveries(endpoint_id, **kwargs)

        return aiterate(_bound, page_size=page_size)

    async def retrieve_delivery(self, endpoint_id: str, delivery_id: str) -> dict[str, Any]:
        """Fetch one delivery record.

        The single-row counterpart to :meth:`list_deliveries`. Carries
        the full response body excerpt rather than the truncated form on
        the list page, which is what you want when debugging one failing
        attempt. Mirrors ``getDelivery`` (node) and ``retrieveDelivery``
        (php).
        """
        return await self._t.request(
            "GET",
            f"/v1/webhook_endpoints/{endpoint_id}/deliveries/{delivery_id}",
        )

    async def redeliver(
        self,
        endpoint_id: str,
        delivery_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Re-enqueue a delivery row for the dispatcher.

        Idempotent: a row already in ``delivered`` returns unchanged.
        ``pending`` / ``failed`` rows flip to ``pending`` with
        ``next_attempt_at = now()`` so the dispatcher picks them up
        on the next tick. ``attempt_count`` is preserved.
        """
        return await self._t.request(
            "POST",
            f"/v1/webhook_endpoints/{endpoint_id}/deliveries/{delivery_id}/redeliver",
            idempotency_key=idempotency_key,
        )


class AsyncEvents:
    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def retrieve(self, event_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/events/{event_id}")

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
        type: str | None = None,
    ) -> dict[str, Any]:
        params = _list_params(
            limit=limit, starting_after=starting_after, ending_before=ending_before
        )
        if type is not None:
            params["type"] = type
        return await self._t.request("GET", "/v1/events", params=params)

    def iter(
        self, *, page_size: int | None = None, type: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each event.

        Pass ``type="customer.created"`` to filter at the server.
        """
        return aiterate(self.list, page_size=page_size, type=type)


class AsyncTenant:
    """Read + mutate tenant-level configuration.

    Today exposes:

    * :meth:`capabilities`: cached Mollie profile shape.
    * :meth:`portal_branding` / :meth:`set_portal_branding`: the
      customer-facing portal chrome (business name, support email,
      logo URL, theme tokens, capability flags).
    * :meth:`rotate_provider_credential`: replace the encrypted
      Mollie API key without re-running ``provision_tenant --force``.
    """

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def capabilities(self) -> dict[str, Any]:
        return await self._t.request("GET", "/v1/tenant/capabilities")

    async def portal_branding(self) -> dict[str, Any]:
        return await self._t.request("GET", "/v1/tenant/portal_branding")

    async def set_portal_branding(
        self,
        *,
        business_name: str | None = None,
        support_email: str | None = None,
        logo_url: str | None = None,
        theme: dict[str, Any] | None = None,
        capabilities: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Partial-update the portal branding row.

        Unset fields are left alone; explicit ``None`` is **not**
        sent (use the raw HTTP path if you need explicit-null clears,
        coming in v0.2). To clear all fields, send empty values.
        """
        body = _drop_none(
            {
                "business_name": business_name,
                "support_email": support_email,
                "logo_url": logo_url,
                "theme": theme,
                "capabilities": capabilities,
            }
        )
        return await self._t.request(
            "POST",
            "/v1/tenant/portal_branding",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def rotate_provider_credential(
        self,
        *,
        api_key: str,
        mode: str | None = None,
        provider: str = "mollie",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Rotate the encrypted provider credential for this tenant.

        ``api_key`` is encrypted server-side; nothing is logged.
        ``mode`` defaults to the calling key's mode. Catches
        prefix-mismatch (``test_...`` under live, ``live_...`` under test)
        at the API boundary.
        """
        body = _drop_none({"api_key": api_key, "mode": mode, "provider": provider})
        return await self._t.request(
            "POST",
            "/v1/tenant/provider_credential",
            json_body=body,
            idempotency_key=idempotency_key,
        )


class AsyncCoupons:
    """Create, validate, and page through promotional codes."""

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def create(
        self,
        *,
        code: str,
        discount_type: str,
        discount_value: int,
        duration: str,
        duration_in_months: int | None = None,
        max_redemptions: int | None = None,
        redeem_by: int | None = None,
        applies_to_price_ids: list[str] | None = None,
        min_amount_cents: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "code": code,
                "discount_type": discount_type,
                "discount_value": discount_value,
                "duration": duration,
                "duration_in_months": duration_in_months,
                "max_redemptions": max_redemptions,
                "redeem_by": redeem_by,
                "applies_to_price_ids": applies_to_price_ids,
                "min_amount_cents": min_amount_cents,
            }
        )
        return await self._t.request(
            "POST", "/v1/coupons", json_body=body, idempotency_key=idempotency_key
        )

    async def retrieve(self, coupon_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/coupons/{coupon_id}")

    async def update(
        self,
        coupon_id: str,
        *,
        active: bool | None = None,
        max_redemptions: int | None = None,
        redeem_by: int | None = None,
        applies_to_price_ids: list[str] | None = None,
        min_amount_cents: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "active": active,
                "max_redemptions": max_redemptions,
                "redeem_by": redeem_by,
                "applies_to_price_ids": applies_to_price_ids,
                "min_amount_cents": min_amount_cents,
            }
        )
        return await self._t.request(
            "POST",
            f"/v1/coupons/{coupon_id}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def delete(
        self, coupon_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._t.request(
            "DELETE", f"/v1/coupons/{coupon_id}", idempotency_key=idempotency_key
        )

    async def validate(
        self,
        *,
        code: str,
        price_id: str | None = None,
        amount_cents: int | None = None,
    ) -> dict[str, Any]:
        """Server-side dry-run of a coupon redemption.

        Returns the discount math without atomically claiming the
        coupon, which is useful for "preview before checkout" UX.

        Only ``code`` is required, matching ``POST /v1/coupons/validate``.
        Omit both optional fields to check the code on its own (exists,
        active, not exhausted, not expired). Supply ``price_id`` to also
        check the coupon's ``applies_to_price_ids`` restriction, and
        ``amount_cents`` to get the discount math plus the
        ``min_amount_cents`` check.
        """
        body: dict[str, Any] = {"code": code}
        if price_id is not None:
            body["price_id"] = price_id
        if amount_cents is not None:
            body["amount_cents"] = amount_cents
        return await self._t.request("POST", "/v1/coupons/validate", json_body=body)

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "GET",
            "/v1/coupons",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> AsyncIterator[dict[str, Any]]:
        return aiterate(self.list, page_size=page_size)


class AsyncTaxRates:
    """Create, update, and page through per-country VAT rates."""

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def create(
        self,
        *,
        country_code: str,
        rate_basis_points: int,
        display_name: str | None = None,
        inclusive: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "country_code": country_code,
                "rate_basis_points": rate_basis_points,
                "display_name": display_name,
                "inclusive": inclusive,
            }
        )
        return await self._t.request(
            "POST", "/v1/tax_rates", json_body=body, idempotency_key=idempotency_key
        )

    async def retrieve(self, tax_rate_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/tax_rates/{tax_rate_id}")

    async def update(
        self,
        tax_rate_id: str,
        *,
        rate_basis_points: int | None = None,
        display_name: str | None = None,
        inclusive: bool | None = None,
        active: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "rate_basis_points": rate_basis_points,
                "display_name": display_name,
                "inclusive": inclusive,
                "active": active,
            }
        )
        return await self._t.request(
            "POST",
            f"/v1/tax_rates/{tax_rate_id}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def delete(
        self, tax_rate_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._t.request(
            "DELETE",
            f"/v1/tax_rates/{tax_rate_id}",
            idempotency_key=idempotency_key,
        )

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "GET",
            "/v1/tax_rates",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> AsyncIterator[dict[str, Any]]:
        return aiterate(self.list, page_size=page_size)


class AsyncInvoices:
    """Read-only access to generated invoices.

    Invoices are produced by the billing pipeline; tenants don't
    create them directly. PDF retrieval issues a 302 redirect to the
    storage adapter's signed URL. Follow it transparently or expose
    it to the customer.
    """

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def retrieve(self, invoice_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/invoices/{invoice_id}")

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "GET",
            "/v1/invoices",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> AsyncIterator[dict[str, Any]]:
        return aiterate(self.list, page_size=page_size)


class AsyncAuditLogs:
    """Read-only access to the per-tenant audit log."""

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def retrieve(self, audit_log_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/audit_logs/{audit_log_id}")

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        params = _list_params(
            limit=limit, starting_after=starting_after, ending_before=ending_before
        )
        for key, value in (
            ("action", action),
            ("resource_type", resource_type),
            ("actor_id", actor_id),
        ):
            if value is not None:
                params[key] = value
        return await self._t.request("GET", "/v1/audit_logs", params=params)

    def iter(
        self,
        *,
        page_size: int | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        actor_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()``; filters are forwarded
        unchanged so ``action="customer.created"`` etc. work."""
        return aiterate(
            self.list,
            page_size=page_size,
            action=action,
            resource_type=resource_type,
            actor_id=actor_id,
        )


class AsyncPayments:
    """Read-only access to the payment ledger.

    Payments are written by the billing pipeline (checkout, renewal,
    reauthorize). Use this resource to inspect attempts and their
    Mollie-side metadata; refunds and disputes are separate flows.
    """

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def retrieve(self, payment_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/payments/{payment_id}")

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "GET",
            "/v1/payments",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> AsyncIterator[dict[str, Any]]:
        return aiterate(self.list, page_size=page_size)


class AsyncBillingPortalSessions:
    """Mint and revoke customer-facing billing-portal sessions.

    Each session token is scoped to a single subscription with a
    sliding 30-minute idle window and 2-hour hard cap. The token is
    returned **once** on mint; the response also includes the URL the
    tenant embeds in their app.
    """

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def create(
        self,
        *,
        subscription_id: str,
        return_url: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "POST",
            "/v1/billing_portal/sessions",
            json_body={"subscription_id": subscription_id, "return_url": return_url},
            idempotency_key=idempotency_key,
        )

    async def revoke(
        self, session_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Kill an in-the-wild portal session. Idempotent."""
        return await self._t.request(
            "POST",
            f"/v1/billing_portal/sessions/{session_id}/revoke",
            idempotency_key=idempotency_key,
        )


# ─── Sync resources ────────────────────────────────────────────────
#
# Sync flavors are mechanical mirrors of the async ones. We keep the
# definitions side-by-side rather than auto-generating from a single
# source because the explicit duplication makes the SDK trivially
# greppable and serves as the documentation of what's supported.


class Customers:
    """Create, update, delete, and page through BillKit customers."""

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def create(
        self,
        *,
        email: str | None = None,
        name: str | None = None,
        country_code: str | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "email": email,
                "name": name,
                "country_code": country_code,
                "metadata": metadata,
            }
        )
        return self._t.request(
            "POST", "/v1/customers", json_body=body, idempotency_key=idempotency_key
        )

    def set_vat_number(
        self,
        customer_id: str,
        *,
        vat_number: str,
        country_code: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Attach or replace the customer's VAT number; triggers VIES validation."""
        body = _drop_none({"vat_number": vat_number, "country_code": country_code})
        return self._t.request(
            "POST",
            f"/v1/customers/{customer_id}/vat_number",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def purge(
        self,
        customer_id: str,
        *,
        confirmed: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Hard-purge a customer's PII for GDPR erasure. Irreversible."""
        return self._t.request(
            "POST",
            f"/v1/customers/{customer_id}/purge",
            json_body={"confirmed": confirmed},
            idempotency_key=idempotency_key,
        )

    def retrieve(self, customer_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/customers/{customer_id}")

    def update(
        self,
        customer_id: str,
        *,
        email: str | None = None,
        name: str | None = None,
        country_code: str | None = None,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "email": email,
                "name": name,
                "country_code": country_code,
                "metadata": metadata,
            }
        )
        return self._t.request(
            "POST",
            f"/v1/customers/{customer_id}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def delete(
        self, customer_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self._t.request(
            "DELETE", f"/v1/customers/{customer_id}", idempotency_key=idempotency_key
        )

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "GET",
            "/v1/customers",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each customer."""
        return paginate(self.list, page_size=page_size)


class Products:
    """Manage catalog products.

    A Product is the customer-facing thing being sold. Create one
    Product, then attach one or more Prices to it.
    """

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def create(
        self,
        *,
        name: str,
        description: str | None = None,
        marketing_features: list[str] | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a catalog product and return the product object."""
        body = _drop_none(
            {
                "name": name,
                "description": description,
                "marketing_features": marketing_features,
                "metadata": metadata,
            }
        )
        return self._t.request(
            "POST", "/v1/products", json_body=body, idempotency_key=idempotency_key
        )

    def retrieve(self, product_id: str) -> dict[str, Any]:
        """Fetch one product by id."""
        return self._t.request("GET", f"/v1/products/{product_id}")

    def update(
        self,
        product_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        marketing_features: list[str] | None = None,
        metadata: dict[str, str] | None = None,
        active: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Patch mutable product fields."""
        body = _drop_none(
            {
                "name": name,
                "description": description,
                "marketing_features": marketing_features,
                "metadata": metadata,
                "active": active,
            }
        )
        return self._t.request(
            "POST",
            f"/v1/products/{product_id}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def delete(self, product_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Archive a product and return its final representation."""
        return self._t.request(
            "DELETE", f"/v1/products/{product_id}", idempotency_key=idempotency_key
        )

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        """List products in reverse creation order."""
        return self._t.request(
            "GET",
            "/v1/products",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each product."""
        return paginate(self.list, page_size=page_size)


class Prices:
    """Manage immutable billing prices attached to products."""

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def create(
        self,
        *,
        product_id: str,
        amount_cents: int,
        currency: str,
        interval: str,
        metadata: dict[str, str] | None = None,
        trial_days: int | None = None,
        trial_verification_cents: int | None = None,
        payment_methods: list[str] | None = None,
        refund_window_initial_days: int | None = None,
        refund_window_renewal_days: int | None = None,
        tax_behavior: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a price for an existing product. See :class:`AsyncPrices`."""
        body = _drop_none(
            {
                "product_id": product_id,
                "amount_cents": amount_cents,
                "currency": currency,
                "interval": interval,
                "metadata": metadata,
                "trial_days": trial_days,
                "trial_verification_cents": trial_verification_cents,
                "payment_methods": payment_methods,
                "refund_window_initial_days": refund_window_initial_days,
                "refund_window_renewal_days": refund_window_renewal_days,
                "tax_behavior": tax_behavior,
            }
        )
        return self._t.request(
            "POST", "/v1/prices", json_body=body, idempotency_key=idempotency_key
        )

    def retrieve(self, price_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/prices/{price_id}")

    def list(
        self,
        *,
        product_id: str | None = None,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        """List prices, optionally narrowed to one product.

        ``product_id`` is applied server-side (``GET
        /v1/prices?product_id=...``), which beats listing everything and
        filtering client-side once a tenant has more than a page of prices.
        """
        params = _list_params(
            limit=limit, starting_after=starting_after, ending_before=ending_before
        )
        if product_id is not None:
            params["product_id"] = product_id
        return self._t.request("GET", "/v1/prices", params=params)

    def iter(
        self, *, product_id: str | None = None, page_size: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each price."""

        def _bound(**kwargs: Any) -> dict[str, Any]:
            return self.list(product_id=product_id, **kwargs)

        return paginate(_bound, page_size=page_size)


class CheckoutSessions:
    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def create(
        self,
        *,
        customer_id: str | None = None,
        customer_email: str | None = None,
        customer_name: str | None = None,
        price_id: str,
        success_url: str,
        cancel_url: str,
        method: str | None = None,
        coupon_code: str | None = None,
        trial_days_override: int | None = None,
        ui_mode: str | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a hosted checkout session. See :class:`AsyncCheckoutSessions`."""
        body = _drop_none(
            {
                "customer_id": customer_id,
                "customer_email": customer_email,
                "customer_name": customer_name,
                "price_id": price_id,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "method": method,
                "coupon_code": coupon_code,
                "trial_days_override": trial_days_override,
                "ui_mode": ui_mode,
                "metadata": metadata,
            }
        )
        return self._t.request(
            "POST",
            "/v1/checkout/sessions",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def retrieve(self, session_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/checkout/sessions/{session_id}")


class OneShotPayments:
    """Sync flavour of :class:`AsyncOneShotPayments`."""

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def create(
        self,
        *,
        customer_id: str,
        amount_cents: int,
        currency: str,
        method: str,
        success_url: str,
        cancel_url: str | None = None,
        description: str | None = None,
        refund_window_days: int | None = None,
        tax_behavior: str | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a one-off charge. See :class:`AsyncOneShotPayments`."""
        body = _drop_none(
            {
                "customer_id": customer_id,
                "amount_cents": amount_cents,
                "currency": currency,
                "method": method,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "description": description,
                "refund_window_days": refund_window_days,
                "tax_behavior": tax_behavior,
                "metadata": metadata,
            }
        )
        return self._t.request(
            "POST", "/v1/checkout/one_shot", json_body=body, idempotency_key=idempotency_key
        )

    def retrieve(self, one_shot_payment_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/checkout/one_shot/{one_shot_payment_id}")


class Subscriptions:
    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def retrieve(self, subscription_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/subscriptions/{subscription_id}")

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "GET",
            "/v1/subscriptions",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each subscription."""
        return paginate(self.list, page_size=page_size)

    def cancel(
        self, subscription_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/v1/subscriptions/{subscription_id}/cancel",
            idempotency_key=idempotency_key,
        )

    def pause(
        self, subscription_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/v1/subscriptions/{subscription_id}/pause",
            idempotency_key=idempotency_key,
        )

    def resume(
        self, subscription_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/v1/subscriptions/{subscription_id}/resume",
            idempotency_key=idempotency_key,
        )

    def reactivate(
        self, subscription_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Reactivate a canceled-but-still-in-period subscription.

        Distinct from :meth:`resume` (paused → active): reactivate
        flips ``canceled`` back to ``active`` for the remainder of the
        current period. Returns 409 if the period has already elapsed.
        """
        return self._t.request(
            "POST",
            f"/v1/subscriptions/{subscription_id}/reactivate",
            idempotency_key=idempotency_key,
        )

    def preview_update(
        self, subscription_id: str, *, target_price_id: str
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/v1/subscriptions/{subscription_id}/preview_update",
            json_body={"target_price_id": target_price_id},
        )

    def update(
        self,
        subscription_id: str,
        *,
        target_price_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/v1/subscriptions/{subscription_id}/update",
            json_body={"target_price_id": target_price_id},
            idempotency_key=idempotency_key,
        )

    def reauthorize_payment_method(
        self,
        subscription_id: str,
        *,
        return_url: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/v1/subscriptions/{subscription_id}/reauthorize_payment_method",
            json_body={"return_url": return_url},
            idempotency_key=idempotency_key,
        )


class Refunds:
    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def create(
        self,
        *,
        payment_id: str | None = None,
        subscription_id: str | None = None,
        one_shot_payment_id: str | None = None,
        amount_cents: int | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        # Pass exactly one target: ``payment_id`` / ``subscription_id`` for a
        # subscription-bound charge, or ``one_shot_payment_id`` for a
        # mandate-less one-shot. The server rejects an ambiguous combination.
        # ``amount_cents`` is optional: omit it to refund the whole remaining
        # balance, or pass a smaller amount for a partial refund (a payment may
        # carry several partials up to the charged amount).
        body = _drop_none(
            {
                "payment_id": payment_id,
                "subscription_id": subscription_id,
                "one_shot_payment_id": one_shot_payment_id,
                "amount_cents": amount_cents,
                "reason": reason,
            }
        )
        return self._t.request(
            "POST", "/v1/refunds", json_body=body, idempotency_key=idempotency_key
        )

    def retrieve(self, refund_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/refunds/{refund_id}")

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "GET",
            "/v1/refunds",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each refund."""
        return paginate(self.list, page_size=page_size)


class Disputes:
    """Chargebacks / disputes. Read-only.

    Disputes are provider-originated (opened by the cardholder's bank) and
    surfaced via the ``dispute.created`` / ``dispute.closed`` webhook events.
    There is no create/update. A dispute's ``status`` is ``open`` or ``won``
    (chargeback reversed); Mollie exposes no "lost" signal, so an upheld
    chargeback stays ``open`` (treat any non-``won`` dispute as unresolved).
    """

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def retrieve(self, dispute_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/disputes/{dispute_id}")

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "GET",
            "/v1/disputes",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each dispute."""
        return paginate(self.list, page_size=page_size)


class WebhookEndpoints:
    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def create(
        self,
        *,
        url: str,
        enabled_events: list[str] | None = None,
        description: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {"url": url, "enabled_events": enabled_events, "description": description}
        )
        return self._t.request(
            "POST",
            "/v1/webhook_endpoints",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def retrieve(self, endpoint_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/webhook_endpoints/{endpoint_id}")

    def update(
        self,
        endpoint_id: str,
        *,
        url: str | None = None,
        enabled_events: list[str] | None = None,
        description: str | None = None,
        status: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "url": url,
                "enabled_events": enabled_events,
                "description": description,
                "status": status,
            }
        )
        return self._t.request(
            "POST",
            f"/v1/webhook_endpoints/{endpoint_id}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def delete(
        self, endpoint_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self._t.request(
            "DELETE",
            f"/v1/webhook_endpoints/{endpoint_id}",
            idempotency_key=idempotency_key,
        )

    def rotate_secret(
        self, endpoint_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/v1/webhook_endpoints/{endpoint_id}/rotate_secret",
            idempotency_key=idempotency_key,
        )

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "GET",
            "/v1/webhook_endpoints",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each endpoint."""
        return paginate(self.list, page_size=page_size)

    def list_deliveries(
        self,
        endpoint_id: str,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        """List per-attempt delivery records for one endpoint."""
        return self._t.request(
            "GET",
            f"/v1/webhook_endpoints/{endpoint_id}/deliveries",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter_deliveries(
        self, endpoint_id: str, *, page_size: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list_deliveries`` for one endpoint."""

        def _bound(**kwargs: Any) -> dict[str, Any]:
            return self.list_deliveries(endpoint_id, **kwargs)

        return paginate(_bound, page_size=page_size)

    def retrieve_delivery(self, endpoint_id: str, delivery_id: str) -> dict[str, Any]:
        """Fetch one delivery record.

        The single-row counterpart to :meth:`list_deliveries`. Carries
        the full response body excerpt rather than the truncated form on
        the list page, which is what you want when debugging one failing
        attempt. Mirrors ``getDelivery`` (node) and ``retrieveDelivery``
        (php).
        """
        return self._t.request(
            "GET",
            f"/v1/webhook_endpoints/{endpoint_id}/deliveries/{delivery_id}",
        )

    def redeliver(
        self,
        endpoint_id: str,
        delivery_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Re-enqueue a delivery row for the dispatcher.

        Idempotent: a row already in ``delivered`` returns unchanged.
        ``pending`` / ``failed`` rows flip to ``pending`` with
        ``next_attempt_at = now()``. ``attempt_count`` is preserved.
        """
        return self._t.request(
            "POST",
            f"/v1/webhook_endpoints/{endpoint_id}/deliveries/{delivery_id}/redeliver",
            idempotency_key=idempotency_key,
        )


class Events:
    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def retrieve(self, event_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/events/{event_id}")

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
        type: str | None = None,
    ) -> dict[str, Any]:
        params = _list_params(
            limit=limit, starting_after=starting_after, ending_before=ending_before
        )
        if type is not None:
            params["type"] = type
        return self._t.request("GET", "/v1/events", params=params)

    def iter(
        self, *, page_size: int | None = None, type: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each event.

        Pass ``type="customer.created"`` to filter at the server.
        """
        return paginate(self.list, page_size=page_size, type=type)


class Tenant:
    """Sync flavour of :class:`AsyncTenant`."""

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def capabilities(self) -> dict[str, Any]:
        return self._t.request("GET", "/v1/tenant/capabilities")

    def portal_branding(self) -> dict[str, Any]:
        return self._t.request("GET", "/v1/tenant/portal_branding")

    def set_portal_branding(
        self,
        *,
        business_name: str | None = None,
        support_email: str | None = None,
        logo_url: str | None = None,
        theme: dict[str, Any] | None = None,
        capabilities: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "business_name": business_name,
                "support_email": support_email,
                "logo_url": logo_url,
                "theme": theme,
                "capabilities": capabilities,
            }
        )
        return self._t.request(
            "POST",
            "/v1/tenant/portal_branding",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def rotate_provider_credential(
        self,
        *,
        api_key: str,
        mode: str | None = None,
        provider: str = "mollie",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Rotate the encrypted provider credential for this tenant."""
        body = _drop_none({"api_key": api_key, "mode": mode, "provider": provider})
        return self._t.request(
            "POST",
            "/v1/tenant/provider_credential",
            json_body=body,
            idempotency_key=idempotency_key,
        )


class Coupons:
    """Sync flavour of :class:`AsyncCoupons`."""

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def create(
        self,
        *,
        code: str,
        discount_type: str,
        discount_value: int,
        duration: str,
        duration_in_months: int | None = None,
        max_redemptions: int | None = None,
        redeem_by: int | None = None,
        applies_to_price_ids: list[str] | None = None,
        min_amount_cents: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "code": code,
                "discount_type": discount_type,
                "discount_value": discount_value,
                "duration": duration,
                "duration_in_months": duration_in_months,
                "max_redemptions": max_redemptions,
                "redeem_by": redeem_by,
                "applies_to_price_ids": applies_to_price_ids,
                "min_amount_cents": min_amount_cents,
            }
        )
        return self._t.request(
            "POST", "/v1/coupons", json_body=body, idempotency_key=idempotency_key
        )

    def retrieve(self, coupon_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/coupons/{coupon_id}")

    def update(
        self,
        coupon_id: str,
        *,
        active: bool | None = None,
        max_redemptions: int | None = None,
        redeem_by: int | None = None,
        applies_to_price_ids: list[str] | None = None,
        min_amount_cents: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "active": active,
                "max_redemptions": max_redemptions,
                "redeem_by": redeem_by,
                "applies_to_price_ids": applies_to_price_ids,
                "min_amount_cents": min_amount_cents,
            }
        )
        return self._t.request(
            "POST",
            f"/v1/coupons/{coupon_id}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def delete(self, coupon_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._t.request(
            "DELETE", f"/v1/coupons/{coupon_id}", idempotency_key=idempotency_key
        )

    def validate(
        self,
        *,
        code: str,
        price_id: str | None = None,
        amount_cents: int | None = None,
    ) -> dict[str, Any]:
        """Server-side dry-run of a coupon redemption.

        Only ``code`` is required, matching ``POST /v1/coupons/validate``.
        See :meth:`AsyncCoupons.validate` for what each optional field adds.
        """
        body: dict[str, Any] = {"code": code}
        if price_id is not None:
            body["price_id"] = price_id
        if amount_cents is not None:
            body["amount_cents"] = amount_cents
        return self._t.request("POST", "/v1/coupons/validate", json_body=body)

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "GET",
            "/v1/coupons",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> Iterator[dict[str, Any]]:
        return paginate(self.list, page_size=page_size)


class TaxRates:
    """Sync flavour of :class:`AsyncTaxRates`."""

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def create(
        self,
        *,
        country_code: str,
        rate_basis_points: int,
        display_name: str | None = None,
        inclusive: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "country_code": country_code,
                "rate_basis_points": rate_basis_points,
                "display_name": display_name,
                "inclusive": inclusive,
            }
        )
        return self._t.request(
            "POST", "/v1/tax_rates", json_body=body, idempotency_key=idempotency_key
        )

    def retrieve(self, tax_rate_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/tax_rates/{tax_rate_id}")

    def update(
        self,
        tax_rate_id: str,
        *,
        rate_basis_points: int | None = None,
        display_name: str | None = None,
        inclusive: bool | None = None,
        active: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "rate_basis_points": rate_basis_points,
                "display_name": display_name,
                "inclusive": inclusive,
                "active": active,
            }
        )
        return self._t.request(
            "POST",
            f"/v1/tax_rates/{tax_rate_id}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def delete(self, tax_rate_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._t.request(
            "DELETE", f"/v1/tax_rates/{tax_rate_id}", idempotency_key=idempotency_key
        )

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "GET",
            "/v1/tax_rates",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> Iterator[dict[str, Any]]:
        return paginate(self.list, page_size=page_size)


class Invoices:
    """Sync flavour of :class:`AsyncInvoices`."""

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def retrieve(self, invoice_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/invoices/{invoice_id}")

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "GET",
            "/v1/invoices",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> Iterator[dict[str, Any]]:
        return paginate(self.list, page_size=page_size)


class AuditLogs:
    """Sync flavour of :class:`AsyncAuditLogs`."""

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def retrieve(self, audit_log_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/audit_logs/{audit_log_id}")

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        params = _list_params(
            limit=limit, starting_after=starting_after, ending_before=ending_before
        )
        for key, value in (
            ("action", action),
            ("resource_type", resource_type),
            ("actor_id", actor_id),
        ):
            if value is not None:
                params[key] = value
        return self._t.request("GET", "/v1/audit_logs", params=params)

    def iter(
        self,
        *,
        page_size: int | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        actor_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        return paginate(
            self.list,
            page_size=page_size,
            action=action,
            resource_type=resource_type,
            actor_id=actor_id,
        )


class Payments:
    """Sync flavour of :class:`AsyncPayments`."""

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def retrieve(self, payment_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/payments/{payment_id}")

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        ending_before: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "GET",
            "/v1/payments",
            params=_list_params(
                limit=limit, starting_after=starting_after, ending_before=ending_before
            ),
        )

    def iter(self, *, page_size: int | None = None) -> Iterator[dict[str, Any]]:
        return paginate(self.list, page_size=page_size)


class BillingPortalSessions:
    """Sync flavour of :class:`AsyncBillingPortalSessions`."""

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def create(
        self,
        *,
        subscription_id: str,
        return_url: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            "/v1/billing_portal/sessions",
            json_body={"subscription_id": subscription_id, "return_url": return_url},
            idempotency_key=idempotency_key,
        )

    def revoke(
        self, session_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/v1/billing_portal/sessions/{session_id}/revoke",
            idempotency_key=idempotency_key,
        )
