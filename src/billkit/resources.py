"""Resource accessors mirroring the BillKit API surface.

Each resource exposes the public verbs from ``/v1/<resource>``. The
return type is ``dict[str, Any]``; the SDK doesn't ship Pydantic
models for responses because the API is Stripe-shape and tenants
typically forward the JSON through to their own data layer
verbatim. Callers who want strong types can wrap the return value
in their own Pydantic models or :class:`typing.TypedDict`.

Every resource appears twice: once as ``Async<Name>`` and once as
``<Name>``. The two halves are an exact mirror — same methods, same
signatures, same docstrings, same bodies, differing only in
``async``/``await``, ``AsyncIterator`` vs ``Iterator``, and ``aiterate``
vs ``paginate``. A caller who reads one half is reading the other.

**Edit the async classes only.** Everything below the "Sync resources"
marker is generated from them by ``scripts/mirror_sync.py``; run it to
propagate a change, and CI runs ``--check`` so the two cannot come
apart between reviews.

The mirror used to be maintained by hand and drifted both ways that a
hand-maintained mirror drifts. Silently, in behaviour: the sync
``Customers.list`` was missing the ``provisional`` filter its async twin
had carried for a release, so the client most Python callers reach for
could not ask the question at all. And loudly, in documentation:
twenty-eight methods and eleven classes explained themselves in full on
the async side and said one line, or nothing, on the sync side.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from enum import Enum
from typing import Any, Final, Protocol
from urllib.parse import quote

from billkit._pagination import aiterate, paginate


class _AsyncRequester(Protocol):
    async def request_bytes(self, method: str, path: str) -> bytes: ...

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
    def request_bytes(self, method: str, path: str) -> bytes: ...

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


def _p(value: str) -> str:
    """Percent-encode a caller-supplied id for use as a path segment.

    Ids reach the SDK from the caller's own storage, and one carrying
    ``/``, ``?`` or ``#`` would otherwise rewrite the request: ``#``
    truncates the path, ``?`` turns the tail into a query string, and
    ``/`` walks to a different route entirely. Encoding keeps the call on
    the route the method names, so a bad id is a clean 404 rather than a
    request somewhere else. ``safe=""`` because nothing in an id is a
    path delimiter here.
    """
    return quote(value, safe="")


def _expand_params(expand: list[str] | None) -> dict[str, Any] | None:
    """Render ``?expand=a,b``, or nothing at all when none was asked for."""
    if not expand:
        return None
    return {"expand": ",".join(expand)}


def _decimal_field(value: str | int | Decimal, *, field: str) -> str:
    """Render a sub-minor-unit rate as the string the API requires.

    The API takes these rates as **strings** and returns them as strings,
    and that is not a stylistic choice: a rate like 0.0002 has no exact
    binary form, so the moment it becomes a float it is a different
    number. JSON has only floats, which is why the field never travels as
    a JSON number in either direction.

    A ``float`` argument is therefore refused outright rather than
    coerced. Accepting one would work for the values that happen to
    round-trip and silently mis-price the ones that do not, which is the
    worst of the three possible behaviours.

    ``Decimal`` is formatted with ``f`` rather than ``str()``: ``str()``
    renders small values in exponent notation (``1E-12``), which the API
    rejects because "1E-12" echoed back as "0.000000000001" would not be
    the string the caller sent.
    """
    if isinstance(value, float):
        raise TypeError(
            f"{field} must be a str, int or Decimal, not a float. A float cannot hold "
            "a rate like 0.0002 exactly, so it would be corrupted before it was ever "
            f'multiplied by a quantity. Pass it as a string: "{value!r}".'
        )
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{field} must be a finite decimal, got {value!r}.")
        return format(value, "f")
    if isinstance(value, bool):
        raise TypeError(f"{field} must be a str, int or Decimal, not a bool.")
    if isinstance(value, int):
        return str(value)
    return value


def _normalize_tiers(tiers: list[dict[str, Any]], *, field: str) -> list[dict[str, Any]]:
    """Copy a tier table, rendering each band's decimal rate as a string.

    Copies rather than mutates: the caller's price definition is often a
    module-level constant, and rewriting its dicts in place would change
    what the *next* call sends.
    """
    out: list[dict[str, Any]] = []
    for index, tier in enumerate(tiers):
        band = dict(tier)
        raw = band.get("unit_amount_decimal")
        if raw is not None:
            band["unit_amount_decimal"] = _decimal_field(
                raw, field=f"{field}[{index}].unit_amount_decimal"
            )
        out.append(band)
    return out


class _Unset(Enum):
    """Sentinel for a keyword that was not passed.

    Only needed where the API distinguishes "leave this alone" from "clear
    it" and spells the second as an explicit JSON ``null``: the tenant
    billing profile, a product's ``default_price_id``, ``description`` and
    ``marketing_features``, a price's two refund windows, a customer's
    ``name``, a webhook endpoint's ``description``, a coupon's
    ``max_redemptions``, ``redeem_by``, ``applies_to_price_ids`` and
    ``min_amount_cents``, and a tax rate's ``display_name``. Everywhere
    else ``None`` means "omit" and :func:`_drop_none` is enough; the API
    refuses a ``null`` there with a 400.
    """

    TOKEN = 0


_UNSET: Final = _Unset.TOKEN


def _put_clearable(body: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """Add each keyword that was passed, ``None`` included, to ``body``.

    ``None`` is sent as a JSON ``null``, which the API reads as "clear";
    a keyword left at :data:`_UNSET` is not sent, which it reads as
    "leave alone".
    """
    for key, value in fields.items():
        if not isinstance(value, _Unset):
            body[key] = value
    return body


def _list_params(*, limit: int | None, starting_after: str | None) -> dict[str, Any]:
    """Build the query for a list call.

    Deliberately only ``limit`` + ``starting_after``: BillKit's cursor
    pagination is forward-only (``api/billkit/api/pagination.py`` binds
    nothing else). An ``ending_before`` used to be accepted here and sent
    on the wire, where the server ignored it and returned page 1 — a
    backwards page that silently re-served the first one.
    """
    return _drop_none({"limit": limit, "starting_after": starting_after})


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
        vat_number: str | None,
        country_code: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Attach, replace, or clear the customer's VAT number.

        Triggers server-side VIES validation. The response carries the
        updated ``vat_number`` plus ``vat_number_validated``; a ``False``
        flag means VIES is reachable but the number didn't validate, or
        the validation is still pending.

        ``vat_number=None`` **clears** the registration, and is sent as an
        explicit JSON null rather than dropped. This is the one body in the
        SDK where ``None`` is a value rather than an omission. VIES
        needs a country, so pass ``country_code`` when the customer does
        not have one yet; that one is omitted when ``None``.
        """
        body: dict[str, Any] = {"vat_number": vat_number}
        if country_code is not None:
            body["country_code"] = country_code
        return await self._t.request(
            "POST",
            f"/v1/customers/{_p(customer_id)}/vat_number",
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
            f"/v1/customers/{_p(customer_id)}/purge",
            json_body={"confirmed": confirmed},
            idempotency_key=idempotency_key,
        )

    async def retrieve(self, customer_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/customers/{_p(customer_id)}")

    async def update(
        self,
        customer_id: str,
        *,
        email: str | None = None,
        name: str | _Unset | None = _UNSET,
        country_code: str | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Patch a customer. Only the keywords you pass change.

        ``metadata`` replaces the whole map. ``name=None`` passed
        explicitly **clears** the name (sent as a JSON null); omit it to
        leave the name alone.
        """
        body = _drop_none(
            {
                "email": email,
                "country_code": country_code,
                "metadata": metadata,
            }
        )
        _put_clearable(body, name=name)
        return await self._t.request(
            "POST",
            f"/v1/customers/{_p(customer_id)}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def delete(
        self, customer_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Delete a customer. Returns ``{"id", "object", "deleted": True}``.

        The customer leaves the API: :meth:`retrieve` 404s and they drop
        out of :meth:`list`, which is why the response is a marker and
        not the customer. Their payments, invoices and refunds are
        untouched, and so is their personal data — :meth:`purge` is the
        GDPR erasure. Refused while they hold a subscription that can
        still charge them.
        """
        return await self._t.request(
            "DELETE", f"/v1/customers/{_p(customer_id)}", idempotency_key=idempotency_key
        )

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        provisional: bool | None = None,
        expand: list[str] | None = None,
    ) -> dict[str, Any]:
        """List customers, newest first.

        ``provisional`` filters on whether the customer ever completed a
        payment. A checkout that captures an email commits its Customer
        before the charge, so a checkout nobody finished leaves a row
        behind: pass ``False`` for real customers only, ``True`` for the
        abandoned ones (the cart-recovery worklist), or omit for both.
        Abandoned rows are swept after the tenant's retention window.

        ``expand`` opts into nested relations, sent as ``expand=a,b``.
        Expandable here: ``stats``. ``customers.retrieve`` accepts none.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        if provisional is not None:
            params["provisional"] = "true" if provisional else "false"
        params.update(_expand_params(expand) or {})
        return await self._t.request("GET", "/v1/customers", params=params)

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
        allow_promotion_codes: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a catalog product and return the product object.

        ``allow_promotion_codes`` lets a *buyer* type a coupon code at the
        embedded checkout for this product. Defaults to ``False``. A coupon
        you apply yourself, by passing ``coupon_code`` when you create a
        Checkout Session, is unaffected — that is you discounting your own
        sale. Either way the code is redeemed only once the payment
        settles, so an abandoned checkout never uses one up.
        """
        body = _drop_none(
            {
                "name": name,
                "description": description,
                "marketing_features": marketing_features,
                "metadata": metadata,
                "allow_promotion_codes": allow_promotion_codes,
            }
        )
        return await self._t.request(
            "POST", "/v1/products", json_body=body, idempotency_key=idempotency_key
        )

    async def retrieve(self, product_id: str, *, expand: list[str] | None = None) -> dict[str, Any]:
        """Fetch one product by id.

        Expandable: ``prices`` (every price on the product, archived ones
        included, active-first), ``stats`` and ``default_price`` (the
        price ``default_price_id`` names).
        """
        return await self._t.request(
            "GET", f"/v1/products/{_p(product_id)}", params=_expand_params(expand)
        )

    async def update(
        self,
        product_id: str,
        *,
        name: str | None = None,
        description: str | _Unset | None = _UNSET,
        marketing_features: list[str] | _Unset | None = _UNSET,
        metadata: dict[str, str] | None = None,
        active: bool | None = None,
        allow_promotion_codes: bool | None = None,
        default_price_id: str | _Unset | None = _UNSET,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Patch mutable product fields, or archive the product.

        Pass only the fields you want to change. ``active=False``
        archives: the product stops being offered, a checkout against
        any of its prices is refused, and it emits ``product.archived``.
        It keeps its id and stays readable, because what was sold under
        it has to be, which is why there is no delete. ``active=True``
        un-archives.

        ``default_price_id`` names the price the billing portal offers on
        that price's interval. It must be an active price of this product;
        anything else raises :class:`InvalidRequestError` on
        ``default_price_id``. Unlike the other keywords here, ``None`` is a
        value for ``default_price_id``, ``description`` and
        ``marketing_features``: pass ``default_price_id=None`` explicitly
        to **clear** the default, ``description=None`` to remove the
        description, or ``marketing_features=None`` to empty the list, and
        omit any of them to leave it alone. ``metadata`` replaces the
        stored object whole, so ``metadata={}`` is how it is emptied.
        """
        body = _drop_none(
            {
                "name": name,
                "metadata": metadata,
                "active": active,
                "allow_promotion_codes": allow_promotion_codes,
            }
        )
        _put_clearable(
            body,
            description=description,
            marketing_features=marketing_features,
            default_price_id=default_price_id,
        )
        return await self._t.request(
            "POST",
            f"/v1/products/{_p(product_id)}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        expand: list[str] | None = None,
    ) -> dict[str, Any]:
        """List products in reverse creation order.

        Expandable: ``prices``, ``stats``, ``default_price``.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        params.update(_expand_params(expand) or {})
        return await self._t.request("GET", "/v1/products", params=params)

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
        amount_cents: int | None = None,
        currency: str,
        interval: str,
        unit_amount_decimal: str | int | Decimal | None = None,
        billing_scheme: str | None = None,
        tiers_mode: str | None = None,
        tiers: list[dict[str, Any]] | None = None,
        metadata: dict[str, str] | None = None,
        trial_days: int | None = None,
        trial_verification_cents: int | None = None,
        payment_methods: list[str] | None = None,
        refund_on_cancel: str | None = None,
        refund_window_initial_days: int | None = None,
        refund_window_renewal_days: int | None = None,
        tax_behavior: str | None = None,
        usage_type: str | None = None,
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

        ``refund_on_cancel`` decides what a cancellation refunds without
        being asked: ``"none"`` (the default) nothing, ``"full"`` the whole
        last charge, ``"prorated"`` the unused part of the current period.
        Both non-none modes also end access immediately, and both are still
        bounded by the refund window. Metered prices must leave this at
        ``"none"`` — see below.

        ``usage_type`` selects the billing model. ``"licensed"`` (the
        default when ``None``) bills ``amount_cents`` per period
        regardless of consumption. ``"metered"`` bills **per reported
        unit**: post consumption with
        :meth:`AsyncSubscriptions.create_usage_record`, and at each period
        close BillKit invoices the period's total and charges the stored
        mandate. Metered prices must be ``interval="month"``, cannot have
        ``trial_days``, and cannot set ``refund_on_cancel`` (ending access
        mid-period would strand usage that has not been billed yet).

        **Three ways to price a metered unit**, and exactly one of them per
        price:

        ``amount_cents``
            Whole minor units per unit. ``amount_cents=5`` is €0.05 each.

        ``unit_amount_decimal``
            A rate finer than one minor unit, in minor units, to 12 decimal
            places. ``"0.02"`` is 0.02 cents, i.e. €0.0002 per unit — the
            canonical per-API-call price, and not expressible as an integer.
            Pass a ``str``, an ``int`` or a ``Decimal``; a ``float`` raises
            ``TypeError``, because a float cannot hold 0.0002 exactly and
            would corrupt the rate before it was ever multiplied. The period's
            whole quantity is multiplied by the rate and rounded **once**, at
            the invoice.

        ``billing_scheme="tiered"`` with ``tiers`` and ``tiers_mode``
            Price by bands. ``tiers_mode="graduated"`` prices the units
            inside each band; ``"volume"`` lets the period total pick one
            band which then prices every unit. The same table under the two
            modes is a different bill, so the mode is required rather than
            defaulted. Each band is a dict: ``up_to`` (a positive int, or
            ``"inf"`` on the last band, which is mandatory because a bounded
            top band cannot price the usage above it), plus ``unit_amount``
            (whole minor units), ``unit_amount_decimal`` (same float rule as
            above) and/or ``flat_amount`` charged once for reaching the band.
            Write a free band as ``unit_amount=0``. A tiered price sends no
            ``amount_cents``::

                await client.prices.create(
                    product_id="prod_api",
                    currency="EUR",
                    interval="month",
                    usage_type="metered",
                    billing_scheme="tiered",
                    tiers_mode="graduated",
                    tiers=[
                        {"up_to": 1000, "unit_amount": 1},
                        {"up_to": "inf", "unit_amount_decimal": "0.5"},
                    ],
                )

        ``amount_cents`` is keyword-optional for that reason, not because it
        is optional in general: a price with none of the three is refused
        server-side with ``parameter_missing``.
        """
        body = _drop_none(
            {
                "product_id": product_id,
                "amount_cents": amount_cents,
                "unit_amount_decimal": (
                    None
                    if unit_amount_decimal is None
                    else _decimal_field(unit_amount_decimal, field="unit_amount_decimal")
                ),
                "currency": currency,
                "interval": interval,
                "billing_scheme": billing_scheme,
                "tiers_mode": tiers_mode,
                "tiers": None if tiers is None else _normalize_tiers(tiers, field="tiers"),
                "metadata": metadata,
                "trial_days": trial_days,
                "trial_verification_cents": trial_verification_cents,
                "payment_methods": payment_methods,
                "refund_on_cancel": refund_on_cancel,
                "refund_window_initial_days": refund_window_initial_days,
                "refund_window_renewal_days": refund_window_renewal_days,
                "tax_behavior": tax_behavior,
                "usage_type": usage_type,
            }
        )
        return await self._t.request(
            "POST", "/v1/prices", json_body=body, idempotency_key=idempotency_key
        )

    async def retrieve(self, price_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/prices/{_p(price_id)}")

    async def update(
        self,
        price_id: str,
        *,
        active: bool | None = None,
        metadata: dict[str, str] | None = None,
        tax_behavior: str | None = None,
        payment_methods: list[str] | None = None,
        refund_on_cancel: str | None = None,
        refund_window_initial_days: int | _Unset | None = _UNSET,
        refund_window_renewal_days: int | _Unset | None = _UNSET,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Change what a price does next. Omitted fields are left alone.

        The dividing line is what a field decides. ``amount_cents``,
        ``currency``, ``interval`` and ``usage_type`` decide **what a past
        charge was**, so they are fixed at creation and absent here:
        subscriptions renew against a price by id, and editing one would
        re-price live customers and make an issued invoice unreadable. To
        charge something different, create a new price.

        Everything accepted here decides **what happens next**.
        ``active=False`` archives the price: it keeps its id and stays
        readable, subscriptions already on it go on renewing, and what
        stops is new business; ``active=True`` undoes that.
        ``payment_methods`` is read when a checkout opens, and the refund
        fields when a cancellation or refund is evaluated, which is the
        useful part: setting ``refund_on_cancel`` covers the customers
        already on the price.

        ``tax_behavior`` is the exception and moves one way. It can be set
        (``"inclusive"`` or ``"exclusive"``) while the price is still
        ``"unspecified"`` and never changed again, because flipping it
        would restate whether tax was inside or on top of an amount
        somebody has already paid.

        ``refund_window_initial_days=None`` and
        ``refund_window_renewal_days=None`` passed explicitly clear the
        price's override (each sent as a JSON null), so the window falls
        back to the default; omit either to leave it alone. On every other
        keyword ``None`` means "not given".

        Sending the value a price already has returns it unchanged and
        emits no second event, which makes a retry safe. Archiving emits
        ``price.archived``; putting one back emits ``price.updated``.
        """
        body = _drop_none(
            {
                "active": active,
                "metadata": metadata,
                "tax_behavior": tax_behavior,
                "payment_methods": payment_methods,
                "refund_on_cancel": refund_on_cancel,
            }
        )
        _put_clearable(
            body,
            refund_window_initial_days=refund_window_initial_days,
            refund_window_renewal_days=refund_window_renewal_days,
        )
        return await self._t.request(
            "POST",
            f"/v1/prices/{_p(price_id)}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def list(
        self,
        *,
        product_id: str | None = None,
        limit: int | None = None,
        starting_after: str | None = None,
    ) -> dict[str, Any]:
        """List prices, optionally narrowed to one product.

        ``product_id`` is applied server-side (``GET
        /v1/prices?product_id=...``), which beats listing everything and
        filtering client-side once a tenant has more than a page of prices.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
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
        country: str | None = None,
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

        ``country`` is the buyer's ISO-3166-1 alpha-2 country, when you
        already know it. It is stored on the customer if they do not have
        one yet, which is what lets VAT apply to the very first charge. On
        the hosted flow the buyer only reaches a country-collecting page
        after the charge exists. It never overwrites a country the
        customer already has.

        ``method`` pins the Mollie payment method (``"creditcard"``,
        ``"directdebit"``, ``"ideal"``, ``"eps"``, ``"applepay"`` or
        ``"paypal"``); ``None`` lets Mollie pick. ``coupon_code`` is
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
                "country": country,
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
        return await self._t.request("GET", f"/v1/checkout/sessions/{_p(session_id)}")


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
        ``bancontact`` / ``banktransfer`` are allowed here even though they
        can't back a subscription; ``banktransfer`` in particular settles
        in days rather than seconds, because the payer is handed bank
        details and Mollie holds the payment open for about a fortnight). ``refund_window_days`` overrides
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
        return await self._t.request("GET", f"/v1/checkout/one_shot/{_p(one_shot_payment_id)}")

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        customer_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """List one-off payments, newest first.

        The counterpart of ``payments.list``, which lists subscription
        payments only. ``customer_id`` narrows to one customer; ``status``
        is one of ``open``, ``pending``, ``authorized``, ``paid``,
        ``failed``, ``expired``, ``canceled`` or ``refunded``, and an
        unknown value raises :class:`InvalidRequestError`. Failed, expired
        and still-open charges are listed too, so check ``status`` before
        treating a row as revenue.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        params.update(_drop_none({"customer_id": customer_id, "status": status}))
        return await self._t.request("GET", "/v1/checkout/one_shot", params=params)

    def iter(
        self,
        *,
        page_size: int | None = None,
        customer_id: str | None = None,
        status: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()``; the filters are carried on each."""
        return aiterate(self.list, page_size=page_size, customer_id=customer_id, status=status)


class AsyncSubscriptions:
    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def retrieve(
        self, subscription_id: str, *, expand: list[str] | None = None
    ) -> dict[str, Any]:
        """Fetch one subscription.

        Expandable: ``customer``, ``price``, ``refund_eligibility``.
        """
        return await self._t.request(
            "GET",
            f"/v1/subscriptions/{_p(subscription_id)}",
            params=_expand_params(expand),
        )

    async def list(
        self,
        *,
        customer_id: str | None = None,
        status: str | None = None,
        renewal_state: str | None = None,
        limit: int | None = None,
        starting_after: str | None = None,
        expand: list[str] | None = None,
    ) -> dict[str, Any]:
        """List subscriptions, newest first, optionally filtered.

        ``status`` and ``renewal_state`` each take a comma-separated
        list (``status="active,past_due"``). An unrecognised value is a
        400 naming the ones that work, rather than being ignored.

        The two answer different questions, and confusing them is the
        usual mistake here. ``status`` is where the subscription stands
        with its payments: ``incomplete``, ``trialing``, ``active``,
        ``past_due``, ``canceled``. ``renewal_state`` is what happens at
        the end of the current period: ``auto_renew``, ``paused``,
        ``canceling``, ``stopped``. A paused subscription still reads as
        ``active``, because the customer has paid for the period they
        are in, so ``renewal_state="paused"`` is how you find paused
        ones. ``status="paused"`` is not accepted and raises
        :class:`~billkit.InvalidRequestError`.

        Expandable: ``customer``, ``price``, ``refund_eligibility``.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        params.update(
            _drop_none(
                {
                    "customer_id": customer_id,
                    "status": status,
                    "renewal_state": renewal_state,
                }
            )
        )
        params.update(_expand_params(expand) or {})
        return await self._t.request("GET", "/v1/subscriptions", params=params)

    def iter(
        self,
        *,
        customer_id: str | None = None,
        status: str | None = None,
        renewal_state: str | None = None,
        page_size: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each subscription.

        The filters are carried onto every page request, so a filtered
        walk narrows server-side instead of paging the whole history and
        discarding rows locally.
        """

        async def _bound(**kwargs: Any) -> dict[str, Any]:
            return await self.list(
                customer_id=customer_id,
                status=status,
                renewal_state=renewal_state,
                **kwargs,
            )

        return aiterate(_bound, page_size=page_size)

    async def cancel(
        self, subscription_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._t.request(
            "POST",
            f"/v1/subscriptions/{_p(subscription_id)}/cancel",
            idempotency_key=idempotency_key,
        )

    async def pause(
        self, subscription_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._t.request(
            "POST",
            f"/v1/subscriptions/{_p(subscription_id)}/pause",
            idempotency_key=idempotency_key,
        )

    async def resume(
        self, subscription_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._t.request(
            "POST",
            f"/v1/subscriptions/{_p(subscription_id)}/resume",
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
            f"/v1/subscriptions/{_p(subscription_id)}/reactivate",
            idempotency_key=idempotency_key,
        )

    async def preview_update(self, subscription_id: str, *, target_price_id: str) -> dict[str, Any]:
        return await self._t.request(
            "POST",
            f"/v1/subscriptions/{_p(subscription_id)}/preview_update",
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
            f"/v1/subscriptions/{_p(subscription_id)}/update",
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
            f"/v1/subscriptions/{_p(subscription_id)}/reauthorize_payment_method",
            json_body={"return_url": return_url},
            idempotency_key=idempotency_key,
        )

    async def create_usage_record(
        self,
        subscription_id: str,
        *,
        quantity: int,
        occurred_at: int | None = None,
        identifier: str | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Report consumption against a metered subscription.

        Only valid when the subscription's price is
        ``usage_type="metered"``; a licensed subscription is rejected
        with ``400 parameter_invalid``. Records accumulate until the next
        period close rolls them into one invoice line; the record's
        ``invoice_id`` stays ``None`` until then.

        ``quantity`` is the number of units consumed (1..1_000_000).
        ``occurred_at`` (epoch seconds) backdates batched reporting;
        omit it to let the server stamp receipt time.

        **Two dedupe mechanisms, for two different failures**, and they are
        not interchangeable:

        ``idempotency_key``
            Covers a retry of *this HTTP request*. The SDK generates one
            per call and reuses it across its own retries, so a timeout
            inside :mod:`billkit` can never double-count.

        ``identifier``
            Covers a retry of *your own call* — a job runner replaying a
            task, a queue delivering twice, your code re-invoking after its
            own timeout. Those arrive at the API as a genuinely new request
            with a new key, so the transport-level key cannot see them.
            Pass the id of whatever you are metering; it is unique within
            the subscription, and a second report of the same identifier
            returns the first record unchanged rather than billing twice.

        If your reporting pipeline is at-least-once, ``identifier`` is the
        one that matters.
        """
        body = _drop_none(
            {
                "quantity": quantity,
                "occurred_at": occurred_at,
                "identifier": identifier,
                "metadata": metadata,
            }
        )
        return await self._t.request(
            "POST",
            f"/v1/subscriptions/{_p(subscription_id)}/usage_records",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def list_usage_records(
        self,
        subscription_id: str,
        *,
        invoice_id: str | None = None,
        limit: int | None = None,
        starting_after: str | None = None,
    ) -> dict[str, Any]:
        """List usage records for one subscription.

        ``invoice_id`` filters by billing state: ``"pending"`` selects
        records not yet rolled into an invoice, and a concrete invoice
        id selects the records that invoice billed. ``None`` lists
        everything.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        if invoice_id is not None:
            params["invoice_id"] = invoice_id
        return await self._t.request(
            "GET", f"/v1/subscriptions/{_p(subscription_id)}/usage_records", params=params
        )

    def iter_usage_records(
        self,
        subscription_id: str,
        *,
        invoice_id: str | None = None,
        page_size: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list_usage_records()`` for one subscription."""

        async def _bound(**kwargs: Any) -> dict[str, Any]:
            return await self.list_usage_records(subscription_id, invoice_id=invoice_id, **kwargs)

        return aiterate(_bound, page_size=page_size)

    async def retrieve_usage_summary(self, subscription_id: str) -> dict[str, Any]:
        """Price the usage that is pending, before the close bills it.

        :meth:`list_usage_records` with ``invoice_id="pending"`` tells you
        the quantity. This tells you the money: ``pending_quantity`` and
        ``pending_record_count``, then ``net_cents`` / ``tax_cents`` /
        ``gross_cents`` computed through the same rate or tier table and the
        same VAT resolution the close itself uses.

        Read ``will_charge`` before you promise a customer an amount. A
        period whose total is under ``minimum_charge_cents`` (€1.00) is not
        charged at all, because the payment provider would refuse it. The
        usage is **not** lost: it stays pending and rolls into the next
        period, which is then billed for both. Without this field the only
        record of that decision was a server log line.

        ``open_invoice_id`` names an earlier cycle that is invoiced and
        still unsettled; while one is open, this period cannot be charged.
        """
        return await self._t.request(
            "GET", f"/v1/subscriptions/{_p(subscription_id)}/usage_summary"
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
        return await self._t.request("GET", f"/v1/refunds/{_p(refund_id)}")

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "GET",
            "/v1/refunds",
            params=_list_params(limit=limit, starting_after=starting_after),
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
        return await self._t.request("GET", f"/v1/disputes/{_p(dispute_id)}")

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        status: str | None = None,
        payment_id: str | None = None,
    ) -> dict[str, Any]:
        """List chargebacks raised against your payments, newest first.

        ``status`` takes a comma-separated list of ``open`` / ``won``.
        There is no ``lost``: the provider gives no signal for one, so a
        dispute you lost stays ``open``. ``payment_id`` matches
        subscription payments only, not one-off charges.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        params.update(_drop_none({"status": status, "payment_id": payment_id}))
        return await self._t.request("GET", "/v1/disputes", params=params)

    def iter(
        self,
        *,
        page_size: int | None = None,
        status: str | None = None,
        payment_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each dispute.

        Filters are carried onto every page request.
        """
        return aiterate(self.list, page_size=page_size, status=status, payment_id=payment_id)


class AsyncWebhookEndpoints:
    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def list_event_types(self) -> dict[str, Any]:
        """Every event type this deployment can deliver, plus the wildcard.

        ``enabled_events`` rejects anything not on this list, so read it
        rather than hard-coding a set: a name that is not on it fails at
        registration and leaves you with an endpoint that never fires.
        Read-only and the same for every caller.
        """
        return await self._t.request("GET", "/v1/webhook_endpoints/event_types")

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
        return await self._t.request("GET", f"/v1/webhook_endpoints/{_p(endpoint_id)}")

    async def update(
        self,
        endpoint_id: str,
        *,
        url: str | None = None,
        enabled_events: list[str] | None = None,
        description: str | _Unset | None = _UNSET,
        status: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Patch an endpoint, or stop delivery with ``status="disabled"``.

        Disabling keeps the endpoint, its signing secret and its delivery
        history, and ``status="enabled"`` resumes. Use :meth:`delete` when
        the endpoint should not exist at all: disabling is reversible and
        deleting is not.

        ``description=None`` passed explicitly **clears** the description
        (sent as a JSON null); omit it to leave the description alone.
        """
        body = _drop_none(
            {
                "url": url,
                "enabled_events": enabled_events,
                "status": status,
            }
        )
        _put_clearable(body, description=description)
        return await self._t.request(
            "POST",
            f"/v1/webhook_endpoints/{_p(endpoint_id)}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def delete(
        self, endpoint_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Delete an endpoint. Returns ``{"deleted": True}``, not the endpoint.

        A URL registered by mistake should not be a permanent fixture of
        the account, so this removes it: :meth:`retrieve` 404s afterwards
        and it is gone from :meth:`list`. Its delivery attempts go with
        it, because they are readable only through the endpoint that owns
        them. The events themselves are untouched and still in
        ``client.events``, so what you were sent stays on record.

        Use :meth:`update` with ``status="disabled"`` if you only want
        delivery to stop.
        """
        return await self._t.request(
            "DELETE",
            f"/v1/webhook_endpoints/{_p(endpoint_id)}",
            idempotency_key=idempotency_key,
        )

    async def rotate_secret(
        self, endpoint_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return await self._t.request(
            "POST",
            f"/v1/webhook_endpoints/{_p(endpoint_id)}/rotate_secret",
            idempotency_key=idempotency_key,
        )

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "GET",
            "/v1/webhook_endpoints",
            params=_list_params(limit=limit, starting_after=starting_after),
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
    ) -> dict[str, Any]:
        """List per-attempt delivery records for one endpoint.

        Useful when a tenant's receiver is failing. Surfaces the
        status code, response body excerpt, error, and next-attempt
        timestamp for each event x endpoint pair.
        """
        return await self._t.request(
            "GET",
            f"/v1/webhook_endpoints/{_p(endpoint_id)}/deliveries",
            params=_list_params(limit=limit, starting_after=starting_after),
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
        attempt. Spelled ``retrieveDelivery`` in the node and php clients.
        """
        return await self._t.request(
            "GET",
            f"/v1/webhook_endpoints/{_p(endpoint_id)}/deliveries/{_p(delivery_id)}",
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
            f"/v1/webhook_endpoints/{_p(endpoint_id)}/deliveries/{_p(delivery_id)}/redeliver",
            idempotency_key=idempotency_key,
        )


class AsyncEvents:
    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def retrieve(self, event_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/events/{_p(event_id)}")

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        type: str | None = None,
        expand: list[str] | None = None,
    ) -> dict[str, Any]:
        """List events, newest first.

        Expandable: ``customer``. ``events.retrieve`` accepts none.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        if type is not None:
            params["type"] = type
        params.update(_expand_params(expand) or {})
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
    * :meth:`billing_profile` / :meth:`set_billing_profile`: your own
      registered country, VAT id and invoice address.
    * :meth:`export`: everything in the account as one JSON document.
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

        Unset fields are left alone: an explicit ``None`` is **not** sent,
        it is dropped like any other omitted keyword. Clear a field by
        sending an empty value for it.
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

    async def billing_profile(self) -> dict[str, Any]:
        """Read your own registered country and VAT number.

        These are what your customers' VAT is decided against, so check
        them before you take your first live payment. ``country_code`` is
        what you have stored and can be ``None``;
        ``effective_country_code`` is what the next charge will really
        use. The two differ only when you have stored nothing, which is
        exactly the case worth spotting. ``vat_id`` has no effective
        counterpart, because nothing can stand in for a registration.
        """
        return await self._t.request("GET", "/v1/tenant/billing_profile")

    async def set_billing_profile(
        self,
        *,
        country_code: str,
        vat_id: str | _Unset | None = _UNSET,
        address_line1: str | _Unset | None = _UNSET,
        address_line2: str | _Unset | None = _UNSET,
        postal_code: str | _Unset | None = _UNSET,
        city: str | _Unset | None = _UNSET,
        registration_number: str | _Unset | None = _UNSET,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Set the seller identity: jurisdiction, VAT id, invoice address.

        ``country_code`` is required on every call: there is nothing to
        leave alone about a jurisdiction, and it decides whether a
        customer's sale is domestic, cross-border within the EU, or
        outside it.

        The address fields and ``registration_number`` are partial-update,
        and here ``None`` is a value rather than an omission, as it is for
        ``products.update(default_price_id=...)``: omit a keyword and the
        stored value is left alone, pass ``None`` explicitly and it is
        **cleared**. Moving office is a real event, so an address that could
        be set once and never emptied would force you to keep printing
        something untrue.

        ``vat_id`` can be set once. After that, a different value or
        ``None`` raises :class:`InvalidRequestError` (``param="vat_id"``,
        reason ``vat_id_locked``) and the call writes nothing; re-sending
        the stored number is accepted. BillKit invoices you reverse-charged
        against it, so support changes it.

        Changes take effect on your next charge only. Tax is worked out
        before money moves and written onto the payment and its invoice,
        so correcting a country here never reprices an issued document.
        """
        body: dict[str, Any] = {"country_code": country_code}
        for key, value in (
            ("vat_id", vat_id),
            ("address_line1", address_line1),
            ("address_line2", address_line2),
            ("postal_code", postal_code),
            ("city", city),
            ("registration_number", registration_number),
        ):
            if not isinstance(value, _Unset):
                body[key] = value
        return await self._t.request(
            "POST",
            "/v1/tenant/billing_profile",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def export(self) -> bytes:
        """Download everything in the account as one JSON document.

        .. code-block:: python

            Path("export.json").write_bytes(client.tenant.export())

        The GDPR Article 20 portability route, and the way to take a
        backup: catalogue, customers, subscriptions, every payment with
        its refunds, credit notes, disputes, invoices with line items,
        usage records and the event log. Each record has the same shape
        its ``GET`` route returns, and ``billkit_export_version`` names
        the shape.

        It is ``application/json`` streamed inline, with no redirect, and it
        can be large, so write it to a file rather than holding it in
        memory. Test and live data export separately: you get whichever
        mode the calling key belongs to. Nothing is changed, but the
        access is recorded in your audit log.
        """
        return await self._t.request_bytes("GET", "/v1/tenant/export")

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
        return await self._t.request("GET", f"/v1/coupons/{_p(coupon_id)}")

    async def update(
        self,
        coupon_id: str,
        *,
        active: bool | None = None,
        max_redemptions: int | _Unset | None = _UNSET,
        redeem_by: int | _Unset | None = _UNSET,
        applies_to_price_ids: list[str] | _Unset | None = _UNSET,
        min_amount_cents: int | _Unset | None = _UNSET,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Patch a coupon's limits, or withdraw it with ``active=False``.

        A withdrawn code is refused at checkout while the coupon stays
        readable and discounts already applied keep working out, which
        is why there is no delete: a redeemed coupon is part of what a
        customer was charged. ``active=True`` brings the campaign back.

        ``max_redemptions=None`` passed explicitly removes the redemption
        cap, ``redeem_by=None`` removes the expiry,
        ``applies_to_price_ids=None`` lifts the price restriction and
        ``min_amount_cents=None`` lifts the minimum (each sent as a JSON
        null); omit any of them to leave it alone.
        """
        body = _drop_none({"active": active})
        _put_clearable(
            body,
            max_redemptions=max_redemptions,
            redeem_by=redeem_by,
            applies_to_price_ids=applies_to_price_ids,
            min_amount_cents=min_amount_cents,
        )
        return await self._t.request(
            "POST",
            f"/v1/coupons/{_p(coupon_id)}",
            json_body=body,
            idempotency_key=idempotency_key,
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
    ) -> dict[str, Any]:
        return await self._t.request(
            "GET",
            "/v1/coupons",
            params=_list_params(limit=limit, starting_after=starting_after),
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
        return await self._t.request("GET", f"/v1/tax_rates/{_p(tax_rate_id)}")

    async def update(
        self,
        tax_rate_id: str,
        *,
        rate_basis_points: int | None = None,
        display_name: str | _Unset | None = _UNSET,
        inclusive: bool | None = None,
        active: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Correct a rate, retire it with ``active=False``, or bring one back.

        Retiring is how you stop charging VAT in a country. The rate
        stays readable, because an invoice records the percentage it
        charged and you have to be able to point at the rate that
        produced it, which is why there is no delete.

        ``display_name=None`` passed explicitly **clears** the display
        name (sent as a JSON null); omit it to leave it alone.
        """
        body = _drop_none(
            {
                "rate_basis_points": rate_basis_points,
                "inclusive": inclusive,
                "active": active,
            }
        )
        _put_clearable(body, display_name=display_name)
        return await self._t.request(
            "POST",
            f"/v1/tax_rates/{_p(tax_rate_id)}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
    ) -> dict[str, Any]:
        return await self._t.request(
            "GET",
            "/v1/tax_rates",
            params=_list_params(limit=limit, starting_after=starting_after),
        )

    def iter(self, *, page_size: int | None = None) -> AsyncIterator[dict[str, Any]]:
        return aiterate(self.list, page_size=page_size)


class AsyncInvoices:
    """Read-only access to generated invoices.

    Invoices are produced by the billing pipeline; tenants don't create
    them directly. :meth:`retrieve_pdf` hands back the rendered bytes: a
    blob-backed deployment streams them inline and an S3-backed one
    answers 302 to a presigned URL, which the transport follows for you,
    so both look identical from here.
    """

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def retrieve(self, invoice_id: str, *, expand: list[str] | None = None) -> dict[str, Any]:
        """Fetch one invoice, line items included.

        Expandable: ``customer``.
        """
        return await self._t.request(
            "GET", f"/v1/invoices/{_p(invoice_id)}", params=_expand_params(expand)
        )

    async def retrieve_pdf(self, invoice_id: str) -> bytes:
        """Download the rendered invoice PDF as raw bytes.

        .. code-block:: python

            pdf = client.invoices.retrieve_pdf("inv_123")
            Path("invoice.pdf").write_bytes(pdf)

        Blob-backed deployments stream the bytes inline; S3-backed ones
        answer ``302`` to a presigned URL, which the transport follows
        under the SDK's own timeout and retry policy — so both storage
        adapters look identical from here.

        Deployments with ``INVOICE_PDF_ENABLED=false`` never render one
        and answer ``501 rendering_pending``, which surfaces as a
        :class:`~billkit.ServerError` whose ``code`` is
        ``"rendering_pending"``; :meth:`retrieve` still returns the
        structured invoice for tenants who render their own.
        """
        return await self._t.request_bytes("GET", f"/v1/invoices/{_p(invoice_id)}/pdf")

    async def send_email(
        self, invoice_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Send the customer their invoice again.

        The same tenant-branded "your invoice is ready" email, with a
        fresh portal link, because the one in the original may have
        expired and re-sending a dead link is worse than not re-sending.

        It goes to the address captured **on the invoice**, not the
        customer's current one: this is a copy of a document that was
        issued to somebody, and quietly redirecting it would make the
        resend a different act from the original send. An invoice with no
        address on file raises :class:`~billkit.InvalidRequestError`
        rather than reporting a send that did not happen.
        """
        return await self._t.request(
            "POST",
            f"/v1/invoices/{_p(invoice_id)}/email",
            idempotency_key=idempotency_key,
        )

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        customer_id: str | None = None,
        subscription_id: str | None = None,
        payment_id: str | None = None,
        status: str | None = None,
        expand: list[str] | None = None,
    ) -> dict[str, Any]:
        """List invoices, newest first. Line items are left out here.

        ``customer_id``, ``subscription_id`` and ``payment_id`` each
        narrow to one, which is how you ask "show me this customer's
        invoices" or "which invoice did this charge produce" without
        paging the whole account. ``status`` takes one of ``draft``,
        ``open``, ``paid``, ``void``, ``uncollectible``.

        Expandable: ``customer``.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        params.update(
            _drop_none(
                {
                    "customer_id": customer_id,
                    "subscription_id": subscription_id,
                    "payment_id": payment_id,
                    "status": status,
                }
            )
        )
        params.update(_expand_params(expand) or {})
        return await self._t.request("GET", "/v1/invoices", params=params)

    def iter(
        self,
        *,
        page_size: int | None = None,
        customer_id: str | None = None,
        subscription_id: str | None = None,
        payment_id: str | None = None,
        status: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()``; filters are carried on each."""
        return aiterate(
            self.list,
            page_size=page_size,
            customer_id=customer_id,
            subscription_id=subscription_id,
            payment_id=payment_id,
            status=status,
        )

    async def void(
        self,
        invoice_id: str,
        *,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Void an invoice: state that the sale was never owed.

        The invoice keeps its number and stays readable — a gapless
        series cannot lose a row — and stops being a receivable. Use it
        for an invoice that should not have been issued.

        A **paid** invoice is refused with :class:`ConflictError` whose
        ``code`` is ``invoice_not_voidable``. That is deliberate: once
        the money has moved, "never owed" is false, and the document that
        reverses a real sale is a credit note — refund the payment and
        one is issued when the refund settles.

        Idempotent: voiding an already-void invoice returns it unchanged.

        ``reason`` is recorded on the audit row only, never on the
        document.
        """
        return await self._t.request(
            "POST",
            f"/v1/invoices/{_p(invoice_id)}/void",
            json_body=_drop_none({"reason": reason}),
            idempotency_key=idempotency_key,
        )


class AsyncCreditNotes:
    """Read-only access to credit notes — the documents that reverse an
    issued invoice.

    There is no create. A credit note is issued for you when a refund
    settles, never on request, so a numbered legal record is only minted
    once the money has actually moved. A refund still pending, a refund
    that fails, and a refund of a one-off charge that was never invoiced
    all produce none.
    """

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def retrieve(self, credit_note_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/credit_notes/{_p(credit_note_id)}")

    async def retrieve_pdf(self, credit_note_id: str) -> bytes:
        """Download the rendered credit note PDF as raw bytes.

        .. code-block:: python

            pdf = client.credit_notes.retrieve_pdf("cn_123")
            Path("credit-note.pdf").write_bytes(pdf)

        Blob-backed deployments stream the bytes inline; S3-backed ones
        answer ``302`` to a presigned URL, which the transport follows
        under the SDK's own timeout and retry policy — so both storage
        adapters look identical from here.

        Deployments with ``INVOICE_PDF_ENABLED=false`` never render one
        and answer ``501 rendering_pending``, which surfaces as a
        :class:`~billkit.ServerError` whose ``code`` is
        ``"rendering_pending"``; :meth:`retrieve` still returns the
        structured credit note for tenants who render their own.
        """
        return await self._t.request_bytes("GET", f"/v1/credit_notes/{_p(credit_note_id)}/pdf")

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        invoice_id: str | None = None,
        customer_id: str | None = None,
    ) -> dict[str, Any]:
        """One page of credit notes, newest first.

        ``invoice_id`` answers "was this sale credited, and by how much",
        which is the question when reconciling a single invoice;
        ``customer_id`` answers it for everything credited to one buyer.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        for key, value in (("invoice_id", invoice_id), ("customer_id", customer_id)):
            if value is not None:
                params[key] = value
        return await self._t.request("GET", "/v1/credit_notes", params=params)

    def iter(
        self,
        *,
        page_size: int | None = None,
        invoice_id: str | None = None,
        customer_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        return aiterate(
            self.list, page_size=page_size, invoice_id=invoice_id, customer_id=customer_id
        )


class AsyncAuditLogs:
    """Read-only access to the per-tenant audit log."""

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def retrieve(self, audit_log_id: str) -> dict[str, Any]:
        return await self._t.request("GET", f"/v1/audit_logs/{_p(audit_log_id)}")

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        """List audit-log entries, newest first, optionally filtered.

        All four filters match exactly and combine. ``resource_type``
        narrows to a kind (``"customer"``, ``"price"``); ``resource_id``
        narrows to one row, which is the "everything that ever happened
        to this customer" question an audit log mostly exists for. Pair
        them or pass ``resource_id`` alone — ids are already unique.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        for key, value in (
            ("action", action),
            ("resource_type", resource_type),
            ("resource_id", resource_id),
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
        resource_id: str | None = None,
        actor_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()``; filters are forwarded
        unchanged so ``action="customer.created"`` etc. work."""
        return aiterate(
            self.list,
            page_size=page_size,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
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

    async def retrieve(self, payment_id: str, *, expand: list[str] | None = None) -> dict[str, Any]:
        """Fetch one subscription payment.

        Expandable: ``customer``, ``subscription``, ``refund_eligibility``.
        ``refund_eligibility`` is retrieve-only (:meth:`list` refuses it)
        and attaches ``{"object": "refund_eligibility", "eligible",
        "amount_cents", "currency", "days_remaining", "window_ends_at",
        "reason"}``: whether a refund of the remaining balance would
        succeed now, applying the refund window and the price's refund
        policy, which ``amount_refundable_cents`` does not. When it would
        not, ``reason`` is ``not_paid``, ``unrefundable_type``,
        ``window_expired``, ``fully_refunded``, ``disputed``,
        ``operation_pending`` or ``plan_change_pending`` (a plan change is
        settling: the full balance cannot be refunded yet, a partial refund
        still can); treat any other value as "not refundable".
        """
        return await self._t.request(
            "GET", f"/v1/payments/{_p(payment_id)}", params=_expand_params(expand)
        )

    async def retrieve_provider(self, payment_id: str) -> dict[str, Any]:
        """Fetch the provider's own record of this charge, live.

        Reads Mollie at request time rather than a stored copy, so it
        carries what BillKit deliberately does not keep: the card BIN, the
        iDEAL bank, the provider's own status string. Reading live means
        it can fail: a provider outage, a credential that no longer
        authorises the profile, or a charge old enough to have aged out
        all answer ``200`` with ``available: False`` and a short reason,
        so render the rest of the page regardless.
        """
        return await self._t.request("GET", f"/v1/payments/{_p(payment_id)}/provider")

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        customer_id: str | None = None,
        expand: list[str] | None = None,
    ) -> dict[str, Any]:
        """List subscription payments, newest first.

        ``customer_id`` narrows to one customer. Failed and pending
        attempts are listed alongside successful ones, so check ``status``
        before treating a row as revenue. Mandate verifications are never
        listed, because nothing was sold, and one-off charges live under
        ``one_shot_payments``.

        Expandable: ``customer``, ``subscription``.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        params.update(_drop_none({"customer_id": customer_id}))
        params.update(_expand_params(expand) or {})
        return await self._t.request("GET", "/v1/payments", params=params)

    def iter(
        self, *, page_size: int | None = None, customer_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()``; ``customer_id`` is carried on each."""
        return aiterate(self.list, page_size=page_size, customer_id=customer_id)


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
        deliver_email: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Mint a portal session for one subscription.

        ``deliver_email=True`` also emails the link to the subscription's
        customer, at the address on their record, as a tenant-branded
        message. It defaults to off: without it you distribute the
        returned ``url`` yourself.
        """
        body = _drop_none(
            {
                "subscription_id": subscription_id,
                "return_url": return_url,
                "deliver_email": deliver_email,
            }
        )
        return await self._t.request(
            "POST",
            "/v1/billing_portal/sessions",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    async def revoke(
        self, session_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Kill an in-the-wild portal session. Idempotent."""
        return await self._t.request(
            "POST",
            f"/v1/billing_portal/sessions/{_p(session_id)}/revoke",
            idempotency_key=idempotency_key,
        )


class AsyncApiKeys:
    """Issue, inspect and revoke API keys.

    A key is issued in the same mode as the key that created it, so a test
    key can only mint test keys, and it can never grant scopes it does not
    hold itself. The secret is returned **once**, on :meth:`create`; every
    later read carries only the prefix.
    """

    def __init__(self, transport: _AsyncRequester) -> None:
        self._t = transport

    async def create(
        self,
        *,
        label: str | None = None,
        scopes: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Issue a new key.

        The response's ``secret`` is the only time the full key exists
        outside your own storage, so record it now; it is never retrievable
        again. ``scopes`` narrows what the key may do, which is the point
        of minting one per integration rather than sharing a single key;
        omit it and the new key inherits the calling key's own. An
        unrecognised scope is rejected at creation rather than failing
        later on every call.
        """
        body = _drop_none({"label": label, "scopes": scopes})
        return await self._t.request(
            "POST", "/v1/api_keys", json_body=body, idempotency_key=idempotency_key
        )

    async def retrieve(self, api_key_id: str) -> dict[str, Any]:
        """One key's metadata: prefix, label, scopes, ``revoked_at``.

        The key itself is never returned. ``last_used_at`` is the useful
        field, since it tells you whether a key is still in service before
        you revoke it.
        """
        return await self._t.request("GET", f"/v1/api_keys/{_p(api_key_id)}")

    async def revoke(
        self, api_key_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Revoke a key so it stops working.

        Immediate and irreversible; issue a new key instead. Revoking an
        already-revoked key returns it unchanged, so a retry is safe, and
        a key may revoke itself, which is what you want when the leaked
        key is the one you are calling with.
        """
        return await self._t.request(
            "POST",
            f"/v1/api_keys/{_p(api_key_id)}/revoke",
            idempotency_key=idempotency_key,
        )

    async def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
    ) -> dict[str, Any]:
        """List your API keys, newest first.

        Only keys in the calling key's mode are listed. Revoked ones are
        included, so check ``revoked_at``.
        """
        return await self._t.request(
            "GET",
            "/v1/api_keys",
            params=_list_params(limit=limit, starting_after=starting_after),
        )

    def iter(self, *, page_size: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each key."""
        return aiterate(self.list, page_size=page_size)


# ─── Sync resources ────────────────────────────────────────────────
#
# GENERATED from the async classes above by `scripts/mirror_sync.py`.
# Do not edit this region by hand: run the script instead, or your change
# is reverted the next time anyone does. `--check` runs in CI.


class Customers:
    """Create, update, delete, and page through BillKit customers.

    Customers are tenant-scoped buyer records. Use them as the anchor
    for checkout sessions, subscriptions, invoices, refunds, and audit
    history. Methods return the API's raw JSON dictionaries so callers
    can preserve fields added by newer API versions.
    """

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
        vat_number: str | None,
        country_code: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Attach, replace, or clear the customer's VAT number.

        Triggers server-side VIES validation. The response carries the
        updated ``vat_number`` plus ``vat_number_validated``; a ``False``
        flag means VIES is reachable but the number didn't validate, or
        the validation is still pending.

        ``vat_number=None`` **clears** the registration, and is sent as an
        explicit JSON null rather than dropped. This is the one body in the
        SDK where ``None`` is a value rather than an omission. VIES
        needs a country, so pass ``country_code`` when the customer does
        not have one yet; that one is omitted when ``None``.
        """
        body: dict[str, Any] = {"vat_number": vat_number}
        if country_code is not None:
            body["country_code"] = country_code
        return self._t.request(
            "POST",
            f"/v1/customers/{_p(customer_id)}/vat_number",
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
        """Hard-purge a customer's PII for GDPR erasure requests.

        Distinct from :meth:`delete` (soft delete): purge nulls email,
        name, country, VAT, and metadata, sets ``purged_at``, and is
        irreversible. ``confirmed=False`` no-ops at the API as a
        fat-finger guard, so the SDK defaults it to ``True``.
        """
        return self._t.request(
            "POST",
            f"/v1/customers/{_p(customer_id)}/purge",
            json_body={"confirmed": confirmed},
            idempotency_key=idempotency_key,
        )

    def retrieve(self, customer_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/customers/{_p(customer_id)}")

    def update(
        self,
        customer_id: str,
        *,
        email: str | None = None,
        name: str | _Unset | None = _UNSET,
        country_code: str | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Patch a customer. Only the keywords you pass change.

        ``metadata`` replaces the whole map. ``name=None`` passed
        explicitly **clears** the name (sent as a JSON null); omit it to
        leave the name alone.
        """
        body = _drop_none(
            {
                "email": email,
                "country_code": country_code,
                "metadata": metadata,
            }
        )
        _put_clearable(body, name=name)
        return self._t.request(
            "POST",
            f"/v1/customers/{_p(customer_id)}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def delete(self, customer_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Delete a customer. Returns ``{"id", "object", "deleted": True}``.

        The customer leaves the API: :meth:`retrieve` 404s and they drop
        out of :meth:`list`, which is why the response is a marker and
        not the customer. Their payments, invoices and refunds are
        untouched, and so is their personal data — :meth:`purge` is the
        GDPR erasure. Refused while they hold a subscription that can
        still charge them.
        """
        return self._t.request(
            "DELETE", f"/v1/customers/{_p(customer_id)}", idempotency_key=idempotency_key
        )

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        provisional: bool | None = None,
        expand: list[str] | None = None,
    ) -> dict[str, Any]:
        """List customers, newest first.

        ``provisional`` filters on whether the customer ever completed a
        payment. A checkout that captures an email commits its Customer
        before the charge, so a checkout nobody finished leaves a row
        behind: pass ``False`` for real customers only, ``True`` for the
        abandoned ones (the cart-recovery worklist), or omit for both.
        Abandoned rows are swept after the tenant's retention window.

        ``expand`` opts into nested relations, sent as ``expand=a,b``.
        Expandable here: ``stats``. ``customers.retrieve`` accepts none.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        if provisional is not None:
            params["provisional"] = "true" if provisional else "false"
        params.update(_expand_params(expand) or {})
        return self._t.request("GET", "/v1/customers", params=params)

    def iter(self, *, page_size: int | None = None) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each customer.

        Page size defaults to the server's default (10). Pass
        ``page_size=100`` to reduce round-trips on large tenant data.
        """
        return paginate(self.list, page_size=page_size)


class Products:
    """Manage catalog products.

    A Product is the customer-facing thing being sold (for example,
    ``"Pro"`` or ``"Enterprise"``). Create one Product, then attach one
    or more Prices to it for currencies, billing intervals, trials, or
    payment-method mixes.
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
        allow_promotion_codes: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a catalog product and return the product object.

        ``allow_promotion_codes`` lets a *buyer* type a coupon code at the
        embedded checkout for this product. Defaults to ``False``. A coupon
        you apply yourself, by passing ``coupon_code`` when you create a
        Checkout Session, is unaffected — that is you discounting your own
        sale. Either way the code is redeemed only once the payment
        settles, so an abandoned checkout never uses one up.
        """
        body = _drop_none(
            {
                "name": name,
                "description": description,
                "marketing_features": marketing_features,
                "metadata": metadata,
                "allow_promotion_codes": allow_promotion_codes,
            }
        )
        return self._t.request(
            "POST", "/v1/products", json_body=body, idempotency_key=idempotency_key
        )

    def retrieve(self, product_id: str, *, expand: list[str] | None = None) -> dict[str, Any]:
        """Fetch one product by id.

        Expandable: ``prices`` (every price on the product, archived ones
        included, active-first), ``stats`` and ``default_price`` (the
        price ``default_price_id`` names).
        """
        return self._t.request(
            "GET", f"/v1/products/{_p(product_id)}", params=_expand_params(expand)
        )

    def update(
        self,
        product_id: str,
        *,
        name: str | None = None,
        description: str | _Unset | None = _UNSET,
        marketing_features: list[str] | _Unset | None = _UNSET,
        metadata: dict[str, str] | None = None,
        active: bool | None = None,
        allow_promotion_codes: bool | None = None,
        default_price_id: str | _Unset | None = _UNSET,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Patch mutable product fields, or archive the product.

        Pass only the fields you want to change. ``active=False``
        archives: the product stops being offered, a checkout against
        any of its prices is refused, and it emits ``product.archived``.
        It keeps its id and stays readable, because what was sold under
        it has to be, which is why there is no delete. ``active=True``
        un-archives.

        ``default_price_id`` names the price the billing portal offers on
        that price's interval. It must be an active price of this product;
        anything else raises :class:`InvalidRequestError` on
        ``default_price_id``. Unlike the other keywords here, ``None`` is a
        value for ``default_price_id``, ``description`` and
        ``marketing_features``: pass ``default_price_id=None`` explicitly
        to **clear** the default, ``description=None`` to remove the
        description, or ``marketing_features=None`` to empty the list, and
        omit any of them to leave it alone. ``metadata`` replaces the
        stored object whole, so ``metadata={}`` is how it is emptied.
        """
        body = _drop_none(
            {
                "name": name,
                "metadata": metadata,
                "active": active,
                "allow_promotion_codes": allow_promotion_codes,
            }
        )
        _put_clearable(
            body,
            description=description,
            marketing_features=marketing_features,
            default_price_id=default_price_id,
        )
        return self._t.request(
            "POST",
            f"/v1/products/{_p(product_id)}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        expand: list[str] | None = None,
    ) -> dict[str, Any]:
        """List products in reverse creation order.

        Expandable: ``prices``, ``stats``, ``default_price``.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        params.update(_expand_params(expand) or {})
        return self._t.request("GET", "/v1/products", params=params)

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
        amount_cents: int | None = None,
        currency: str,
        interval: str,
        unit_amount_decimal: str | int | Decimal | None = None,
        billing_scheme: str | None = None,
        tiers_mode: str | None = None,
        tiers: list[dict[str, Any]] | None = None,
        metadata: dict[str, str] | None = None,
        trial_days: int | None = None,
        trial_verification_cents: int | None = None,
        payment_methods: list[str] | None = None,
        refund_on_cancel: str | None = None,
        refund_window_initial_days: int | None = None,
        refund_window_renewal_days: int | None = None,
        tax_behavior: str | None = None,
        usage_type: str | None = None,
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

        ``refund_on_cancel`` decides what a cancellation refunds without
        being asked: ``"none"`` (the default) nothing, ``"full"`` the whole
        last charge, ``"prorated"`` the unused part of the current period.
        Both non-none modes also end access immediately, and both are still
        bounded by the refund window. Metered prices must leave this at
        ``"none"`` — see below.

        ``usage_type`` selects the billing model. ``"licensed"`` (the
        default when ``None``) bills ``amount_cents`` per period
        regardless of consumption. ``"metered"`` bills **per reported
        unit**: post consumption with
        :meth:`Subscriptions.create_usage_record`, and at each period
        close BillKit invoices the period's total and charges the stored
        mandate. Metered prices must be ``interval="month"``, cannot have
        ``trial_days``, and cannot set ``refund_on_cancel`` (ending access
        mid-period would strand usage that has not been billed yet).

        **Three ways to price a metered unit**, and exactly one of them per
        price:

        ``amount_cents``
            Whole minor units per unit. ``amount_cents=5`` is €0.05 each.

        ``unit_amount_decimal``
            A rate finer than one minor unit, in minor units, to 12 decimal
            places. ``"0.02"`` is 0.02 cents, i.e. €0.0002 per unit — the
            canonical per-API-call price, and not expressible as an integer.
            Pass a ``str``, an ``int`` or a ``Decimal``; a ``float`` raises
            ``TypeError``, because a float cannot hold 0.0002 exactly and
            would corrupt the rate before it was ever multiplied. The period's
            whole quantity is multiplied by the rate and rounded **once**, at
            the invoice.

        ``billing_scheme="tiered"`` with ``tiers`` and ``tiers_mode``
            Price by bands. ``tiers_mode="graduated"`` prices the units
            inside each band; ``"volume"`` lets the period total pick one
            band which then prices every unit. The same table under the two
            modes is a different bill, so the mode is required rather than
            defaulted. Each band is a dict: ``up_to`` (a positive int, or
            ``"inf"`` on the last band, which is mandatory because a bounded
            top band cannot price the usage above it), plus ``unit_amount``
            (whole minor units), ``unit_amount_decimal`` (same float rule as
            above) and/or ``flat_amount`` charged once for reaching the band.
            Write a free band as ``unit_amount=0``. A tiered price sends no
            ``amount_cents``::

                client.prices.create(
                    product_id="prod_api",
                    currency="EUR",
                    interval="month",
                    usage_type="metered",
                    billing_scheme="tiered",
                    tiers_mode="graduated",
                    tiers=[
                        {"up_to": 1000, "unit_amount": 1},
                        {"up_to": "inf", "unit_amount_decimal": "0.5"},
                    ],
                )

        ``amount_cents`` is keyword-optional for that reason, not because it
        is optional in general: a price with none of the three is refused
        server-side with ``parameter_missing``.
        """
        body = _drop_none(
            {
                "product_id": product_id,
                "amount_cents": amount_cents,
                "unit_amount_decimal": (
                    None
                    if unit_amount_decimal is None
                    else _decimal_field(unit_amount_decimal, field="unit_amount_decimal")
                ),
                "currency": currency,
                "interval": interval,
                "billing_scheme": billing_scheme,
                "tiers_mode": tiers_mode,
                "tiers": None if tiers is None else _normalize_tiers(tiers, field="tiers"),
                "metadata": metadata,
                "trial_days": trial_days,
                "trial_verification_cents": trial_verification_cents,
                "payment_methods": payment_methods,
                "refund_on_cancel": refund_on_cancel,
                "refund_window_initial_days": refund_window_initial_days,
                "refund_window_renewal_days": refund_window_renewal_days,
                "tax_behavior": tax_behavior,
                "usage_type": usage_type,
            }
        )
        return self._t.request(
            "POST", "/v1/prices", json_body=body, idempotency_key=idempotency_key
        )

    def retrieve(self, price_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/prices/{_p(price_id)}")

    def update(
        self,
        price_id: str,
        *,
        active: bool | None = None,
        metadata: dict[str, str] | None = None,
        tax_behavior: str | None = None,
        payment_methods: list[str] | None = None,
        refund_on_cancel: str | None = None,
        refund_window_initial_days: int | _Unset | None = _UNSET,
        refund_window_renewal_days: int | _Unset | None = _UNSET,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Change what a price does next. Omitted fields are left alone.

        The dividing line is what a field decides. ``amount_cents``,
        ``currency``, ``interval`` and ``usage_type`` decide **what a past
        charge was**, so they are fixed at creation and absent here:
        subscriptions renew against a price by id, and editing one would
        re-price live customers and make an issued invoice unreadable. To
        charge something different, create a new price.

        Everything accepted here decides **what happens next**.
        ``active=False`` archives the price: it keeps its id and stays
        readable, subscriptions already on it go on renewing, and what
        stops is new business; ``active=True`` undoes that.
        ``payment_methods`` is read when a checkout opens, and the refund
        fields when a cancellation or refund is evaluated, which is the
        useful part: setting ``refund_on_cancel`` covers the customers
        already on the price.

        ``tax_behavior`` is the exception and moves one way. It can be set
        (``"inclusive"`` or ``"exclusive"``) while the price is still
        ``"unspecified"`` and never changed again, because flipping it
        would restate whether tax was inside or on top of an amount
        somebody has already paid.

        ``refund_window_initial_days=None`` and
        ``refund_window_renewal_days=None`` passed explicitly clear the
        price's override (each sent as a JSON null), so the window falls
        back to the default; omit either to leave it alone. On every other
        keyword ``None`` means "not given".

        Sending the value a price already has returns it unchanged and
        emits no second event, which makes a retry safe. Archiving emits
        ``price.archived``; putting one back emits ``price.updated``.
        """
        body = _drop_none(
            {
                "active": active,
                "metadata": metadata,
                "tax_behavior": tax_behavior,
                "payment_methods": payment_methods,
                "refund_on_cancel": refund_on_cancel,
            }
        )
        _put_clearable(
            body,
            refund_window_initial_days=refund_window_initial_days,
            refund_window_renewal_days=refund_window_renewal_days,
        )
        return self._t.request(
            "POST",
            f"/v1/prices/{_p(price_id)}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def list(
        self,
        *,
        product_id: str | None = None,
        limit: int | None = None,
        starting_after: str | None = None,
    ) -> dict[str, Any]:
        """List prices, optionally narrowed to one product.

        ``product_id`` is applied server-side (``GET
        /v1/prices?product_id=...``), which beats listing everything and
        filtering client-side once a tenant has more than a page of prices.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
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
        country: str | None = None,
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

        ``country`` is the buyer's ISO-3166-1 alpha-2 country, when you
        already know it. It is stored on the customer if they do not have
        one yet, which is what lets VAT apply to the very first charge. On
        the hosted flow the buyer only reaches a country-collecting page
        after the charge exists. It never overwrites a country the
        customer already has.

        ``method`` pins the Mollie payment method (``"creditcard"``,
        ``"directdebit"``, ``"ideal"``, ``"eps"``, ``"applepay"`` or
        ``"paypal"``); ``None`` lets Mollie pick. ``coupon_code`` is
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
                "country": country,
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
        return self._t.request("GET", f"/v1/checkout/sessions/{_p(session_id)}")


class OneShotPayments:
    """Mandate-less one-shot payments (``/v1/checkout/one_shot``).

    A one-shot is the Stripe PaymentIntent shape mapped onto Mollie: a
    single ``sequenceType=oneoff`` charge that provisions nothing: no
    subscription, no mandate, no renewals. Drive terminal state via the
    ``one_shot_payment.succeeded`` / ``.failed`` webhook events; refund
    one with ``refunds.create(one_shot_payment_id=...)``.
    """

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
        """Create a one-off charge.

        ``method`` is required and validated against the tenant's Mollie
        capability allowlist for ``currency`` (one-off-only methods like
        ``bancontact`` / ``banktransfer`` are allowed here even though they
        can't back a subscription; ``banktransfer`` in particular settles
        in days rather than seconds, because the payer is handed bank
        details and Mollie holds the payment open for about a fortnight). ``refund_window_days`` overrides
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
        return self._t.request(
            "POST", "/v1/checkout/one_shot", json_body=body, idempotency_key=idempotency_key
        )

    def retrieve(self, one_shot_payment_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/checkout/one_shot/{_p(one_shot_payment_id)}")

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        customer_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """List one-off payments, newest first.

        The counterpart of ``payments.list``, which lists subscription
        payments only. ``customer_id`` narrows to one customer; ``status``
        is one of ``open``, ``pending``, ``authorized``, ``paid``,
        ``failed``, ``expired``, ``canceled`` or ``refunded``, and an
        unknown value raises :class:`InvalidRequestError`. Failed, expired
        and still-open charges are listed too, so check ``status`` before
        treating a row as revenue.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        params.update(_drop_none({"customer_id": customer_id, "status": status}))
        return self._t.request("GET", "/v1/checkout/one_shot", params=params)

    def iter(
        self,
        *,
        page_size: int | None = None,
        customer_id: str | None = None,
        status: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()``; the filters are carried on each."""
        return paginate(self.list, page_size=page_size, customer_id=customer_id, status=status)


class Subscriptions:
    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def retrieve(self, subscription_id: str, *, expand: list[str] | None = None) -> dict[str, Any]:
        """Fetch one subscription.

        Expandable: ``customer``, ``price``, ``refund_eligibility``.
        """
        return self._t.request(
            "GET",
            f"/v1/subscriptions/{_p(subscription_id)}",
            params=_expand_params(expand),
        )

    def list(
        self,
        *,
        customer_id: str | None = None,
        status: str | None = None,
        renewal_state: str | None = None,
        limit: int | None = None,
        starting_after: str | None = None,
        expand: list[str] | None = None,
    ) -> dict[str, Any]:
        """List subscriptions, newest first, optionally filtered.

        ``status`` and ``renewal_state`` each take a comma-separated
        list (``status="active,past_due"``). An unrecognised value is a
        400 naming the ones that work, rather than being ignored.

        The two answer different questions, and confusing them is the
        usual mistake here. ``status`` is where the subscription stands
        with its payments: ``incomplete``, ``trialing``, ``active``,
        ``past_due``, ``canceled``. ``renewal_state`` is what happens at
        the end of the current period: ``auto_renew``, ``paused``,
        ``canceling``, ``stopped``. A paused subscription still reads as
        ``active``, because the customer has paid for the period they
        are in, so ``renewal_state="paused"`` is how you find paused
        ones. ``status="paused"`` is not accepted and raises
        :class:`~billkit.InvalidRequestError`.

        Expandable: ``customer``, ``price``, ``refund_eligibility``.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        params.update(
            _drop_none(
                {
                    "customer_id": customer_id,
                    "status": status,
                    "renewal_state": renewal_state,
                }
            )
        )
        params.update(_expand_params(expand) or {})
        return self._t.request("GET", "/v1/subscriptions", params=params)

    def iter(
        self,
        *,
        customer_id: str | None = None,
        status: str | None = None,
        renewal_state: str | None = None,
        page_size: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each subscription.

        The filters are carried onto every page request, so a filtered
        walk narrows server-side instead of paging the whole history and
        discarding rows locally.
        """

        def _bound(**kwargs: Any) -> dict[str, Any]:
            return self.list(
                customer_id=customer_id,
                status=status,
                renewal_state=renewal_state,
                **kwargs,
            )

        return paginate(_bound, page_size=page_size)

    def cancel(self, subscription_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/v1/subscriptions/{_p(subscription_id)}/cancel",
            idempotency_key=idempotency_key,
        )

    def pause(self, subscription_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/v1/subscriptions/{_p(subscription_id)}/pause",
            idempotency_key=idempotency_key,
        )

    def resume(self, subscription_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/v1/subscriptions/{_p(subscription_id)}/resume",
            idempotency_key=idempotency_key,
        )

    def reactivate(
        self, subscription_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Reactivate a subscription that's been canceled but is still
        inside its paid-through period.

        Distinct from :meth:`resume` (paused → active): reactivate
        flips ``canceled`` back to ``active`` for the remainder of the
        current period, so the customer keeps service without a new
        checkout. Returns 409 if the period has already elapsed.
        """
        return self._t.request(
            "POST",
            f"/v1/subscriptions/{_p(subscription_id)}/reactivate",
            idempotency_key=idempotency_key,
        )

    def preview_update(self, subscription_id: str, *, target_price_id: str) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/v1/subscriptions/{_p(subscription_id)}/preview_update",
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
            f"/v1/subscriptions/{_p(subscription_id)}/update",
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
            f"/v1/subscriptions/{_p(subscription_id)}/reauthorize_payment_method",
            json_body={"return_url": return_url},
            idempotency_key=idempotency_key,
        )

    def create_usage_record(
        self,
        subscription_id: str,
        *,
        quantity: int,
        occurred_at: int | None = None,
        identifier: str | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Report consumption against a metered subscription.

        Only valid when the subscription's price is
        ``usage_type="metered"``; a licensed subscription is rejected
        with ``400 parameter_invalid``. Records accumulate until the next
        period close rolls them into one invoice line; the record's
        ``invoice_id`` stays ``None`` until then.

        ``quantity`` is the number of units consumed (1..1_000_000).
        ``occurred_at`` (epoch seconds) backdates batched reporting;
        omit it to let the server stamp receipt time.

        **Two dedupe mechanisms, for two different failures**, and they are
        not interchangeable:

        ``idempotency_key``
            Covers a retry of *this HTTP request*. The SDK generates one
            per call and reuses it across its own retries, so a timeout
            inside :mod:`billkit` can never double-count.

        ``identifier``
            Covers a retry of *your own call* — a job runner replaying a
            task, a queue delivering twice, your code re-invoking after its
            own timeout. Those arrive at the API as a genuinely new request
            with a new key, so the transport-level key cannot see them.
            Pass the id of whatever you are metering; it is unique within
            the subscription, and a second report of the same identifier
            returns the first record unchanged rather than billing twice.

        If your reporting pipeline is at-least-once, ``identifier`` is the
        one that matters.
        """
        body = _drop_none(
            {
                "quantity": quantity,
                "occurred_at": occurred_at,
                "identifier": identifier,
                "metadata": metadata,
            }
        )
        return self._t.request(
            "POST",
            f"/v1/subscriptions/{_p(subscription_id)}/usage_records",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def list_usage_records(
        self,
        subscription_id: str,
        *,
        invoice_id: str | None = None,
        limit: int | None = None,
        starting_after: str | None = None,
    ) -> dict[str, Any]:
        """List usage records for one subscription.

        ``invoice_id`` filters by billing state: ``"pending"`` selects
        records not yet rolled into an invoice, and a concrete invoice
        id selects the records that invoice billed. ``None`` lists
        everything.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        if invoice_id is not None:
            params["invoice_id"] = invoice_id
        return self._t.request(
            "GET", f"/v1/subscriptions/{_p(subscription_id)}/usage_records", params=params
        )

    def iter_usage_records(
        self,
        subscription_id: str,
        *,
        invoice_id: str | None = None,
        page_size: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list_usage_records()`` for one subscription."""

        def _bound(**kwargs: Any) -> dict[str, Any]:
            return self.list_usage_records(subscription_id, invoice_id=invoice_id, **kwargs)

        return paginate(_bound, page_size=page_size)

    def retrieve_usage_summary(self, subscription_id: str) -> dict[str, Any]:
        """Price the usage that is pending, before the close bills it.

        :meth:`list_usage_records` with ``invoice_id="pending"`` tells you
        the quantity. This tells you the money: ``pending_quantity`` and
        ``pending_record_count``, then ``net_cents`` / ``tax_cents`` /
        ``gross_cents`` computed through the same rate or tier table and the
        same VAT resolution the close itself uses.

        Read ``will_charge`` before you promise a customer an amount. A
        period whose total is under ``minimum_charge_cents`` (€1.00) is not
        charged at all, because the payment provider would refuse it. The
        usage is **not** lost: it stays pending and rolls into the next
        period, which is then billed for both. Without this field the only
        record of that decision was a server log line.

        ``open_invoice_id`` names an earlier cycle that is invoiced and
        still unsettled; while one is open, this period cannot be charged.
        """
        return self._t.request("GET", f"/v1/subscriptions/{_p(subscription_id)}/usage_summary")


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
        return self._t.request("GET", f"/v1/refunds/{_p(refund_id)}")

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "GET",
            "/v1/refunds",
            params=_list_params(limit=limit, starting_after=starting_after),
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
        return self._t.request("GET", f"/v1/disputes/{_p(dispute_id)}")

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        status: str | None = None,
        payment_id: str | None = None,
    ) -> dict[str, Any]:
        """List chargebacks raised against your payments, newest first.

        ``status`` takes a comma-separated list of ``open`` / ``won``.
        There is no ``lost``: the provider gives no signal for one, so a
        dispute you lost stays ``open``. ``payment_id`` matches
        subscription payments only, not one-off charges.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        params.update(_drop_none({"status": status, "payment_id": payment_id}))
        return self._t.request("GET", "/v1/disputes", params=params)

    def iter(
        self,
        *,
        page_size: int | None = None,
        status: str | None = None,
        payment_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each dispute.

        Filters are carried onto every page request.
        """
        return paginate(self.list, page_size=page_size, status=status, payment_id=payment_id)


class WebhookEndpoints:
    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def list_event_types(self) -> dict[str, Any]:
        """Every event type this deployment can deliver, plus the wildcard.

        ``enabled_events`` rejects anything not on this list, so read it
        rather than hard-coding a set: a name that is not on it fails at
        registration and leaves you with an endpoint that never fires.
        Read-only and the same for every caller.
        """
        return self._t.request("GET", "/v1/webhook_endpoints/event_types")

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
        return self._t.request("GET", f"/v1/webhook_endpoints/{_p(endpoint_id)}")

    def update(
        self,
        endpoint_id: str,
        *,
        url: str | None = None,
        enabled_events: list[str] | None = None,
        description: str | _Unset | None = _UNSET,
        status: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Patch an endpoint, or stop delivery with ``status="disabled"``.

        Disabling keeps the endpoint, its signing secret and its delivery
        history, and ``status="enabled"`` resumes. Use :meth:`delete` when
        the endpoint should not exist at all: disabling is reversible and
        deleting is not.

        ``description=None`` passed explicitly **clears** the description
        (sent as a JSON null); omit it to leave the description alone.
        """
        body = _drop_none(
            {
                "url": url,
                "enabled_events": enabled_events,
                "status": status,
            }
        )
        _put_clearable(body, description=description)
        return self._t.request(
            "POST",
            f"/v1/webhook_endpoints/{_p(endpoint_id)}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def delete(self, endpoint_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Delete an endpoint. Returns ``{"deleted": True}``, not the endpoint.

        A URL registered by mistake should not be a permanent fixture of
        the account, so this removes it: :meth:`retrieve` 404s afterwards
        and it is gone from :meth:`list`. Its delivery attempts go with
        it, because they are readable only through the endpoint that owns
        them. The events themselves are untouched and still in
        ``client.events``, so what you were sent stays on record.

        Use :meth:`update` with ``status="disabled"`` if you only want
        delivery to stop.
        """
        return self._t.request(
            "DELETE",
            f"/v1/webhook_endpoints/{_p(endpoint_id)}",
            idempotency_key=idempotency_key,
        )

    def rotate_secret(
        self, endpoint_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self._t.request(
            "POST",
            f"/v1/webhook_endpoints/{_p(endpoint_id)}/rotate_secret",
            idempotency_key=idempotency_key,
        )

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "GET",
            "/v1/webhook_endpoints",
            params=_list_params(limit=limit, starting_after=starting_after),
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
    ) -> dict[str, Any]:
        """List per-attempt delivery records for one endpoint.

        Useful when a tenant's receiver is failing. Surfaces the
        status code, response body excerpt, error, and next-attempt
        timestamp for each event x endpoint pair.
        """
        return self._t.request(
            "GET",
            f"/v1/webhook_endpoints/{_p(endpoint_id)}/deliveries",
            params=_list_params(limit=limit, starting_after=starting_after),
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
        attempt. Spelled ``retrieveDelivery`` in the node and php clients.
        """
        return self._t.request(
            "GET",
            f"/v1/webhook_endpoints/{_p(endpoint_id)}/deliveries/{_p(delivery_id)}",
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
        ``next_attempt_at = now()`` so the dispatcher picks them up
        on the next tick. ``attempt_count`` is preserved.
        """
        return self._t.request(
            "POST",
            f"/v1/webhook_endpoints/{_p(endpoint_id)}/deliveries/{_p(delivery_id)}/redeliver",
            idempotency_key=idempotency_key,
        )


class Events:
    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def retrieve(self, event_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/events/{_p(event_id)}")

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        type: str | None = None,
        expand: list[str] | None = None,
    ) -> dict[str, Any]:
        """List events, newest first.

        Expandable: ``customer``. ``events.retrieve`` accepts none.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        if type is not None:
            params["type"] = type
        params.update(_expand_params(expand) or {})
        return self._t.request("GET", "/v1/events", params=params)

    def iter(
        self, *, page_size: int | None = None, type: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each event.

        Pass ``type="customer.created"`` to filter at the server.
        """
        return paginate(self.list, page_size=page_size, type=type)


class Tenant:
    """Read + mutate tenant-level configuration.

    Today exposes:

    * :meth:`capabilities`: cached Mollie profile shape.
    * :meth:`portal_branding` / :meth:`set_portal_branding`: the
      customer-facing portal chrome (business name, support email,
      logo URL, theme tokens, capability flags).
    * :meth:`billing_profile` / :meth:`set_billing_profile`: your own
      registered country, VAT id and invoice address.
    * :meth:`export`: everything in the account as one JSON document.
    * :meth:`rotate_provider_credential`: replace the encrypted
      Mollie API key without re-running ``provision_tenant --force``.
    """

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
        """Partial-update the portal branding row.

        Unset fields are left alone: an explicit ``None`` is **not** sent,
        it is dropped like any other omitted keyword. Clear a field by
        sending an empty value for it.
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
        return self._t.request(
            "POST",
            "/v1/tenant/portal_branding",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def billing_profile(self) -> dict[str, Any]:
        """Read your own registered country and VAT number.

        These are what your customers' VAT is decided against, so check
        them before you take your first live payment. ``country_code`` is
        what you have stored and can be ``None``;
        ``effective_country_code`` is what the next charge will really
        use. The two differ only when you have stored nothing, which is
        exactly the case worth spotting. ``vat_id`` has no effective
        counterpart, because nothing can stand in for a registration.
        """
        return self._t.request("GET", "/v1/tenant/billing_profile")

    def set_billing_profile(
        self,
        *,
        country_code: str,
        vat_id: str | _Unset | None = _UNSET,
        address_line1: str | _Unset | None = _UNSET,
        address_line2: str | _Unset | None = _UNSET,
        postal_code: str | _Unset | None = _UNSET,
        city: str | _Unset | None = _UNSET,
        registration_number: str | _Unset | None = _UNSET,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Set the seller identity: jurisdiction, VAT id, invoice address.

        ``country_code`` is required on every call: there is nothing to
        leave alone about a jurisdiction, and it decides whether a
        customer's sale is domestic, cross-border within the EU, or
        outside it.

        The address fields and ``registration_number`` are partial-update,
        and here ``None`` is a value rather than an omission, as it is for
        ``products.update(default_price_id=...)``: omit a keyword and the
        stored value is left alone, pass ``None`` explicitly and it is
        **cleared**. Moving office is a real event, so an address that could
        be set once and never emptied would force you to keep printing
        something untrue.

        ``vat_id`` can be set once. After that, a different value or
        ``None`` raises :class:`InvalidRequestError` (``param="vat_id"``,
        reason ``vat_id_locked``) and the call writes nothing; re-sending
        the stored number is accepted. BillKit invoices you reverse-charged
        against it, so support changes it.

        Changes take effect on your next charge only. Tax is worked out
        before money moves and written onto the payment and its invoice,
        so correcting a country here never reprices an issued document.
        """
        body: dict[str, Any] = {"country_code": country_code}
        for key, value in (
            ("vat_id", vat_id),
            ("address_line1", address_line1),
            ("address_line2", address_line2),
            ("postal_code", postal_code),
            ("city", city),
            ("registration_number", registration_number),
        ):
            if not isinstance(value, _Unset):
                body[key] = value
        return self._t.request(
            "POST",
            "/v1/tenant/billing_profile",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def export(self) -> bytes:
        """Download everything in the account as one JSON document.

        .. code-block:: python

            Path("export.json").write_bytes(client.tenant.export())

        The GDPR Article 20 portability route, and the way to take a
        backup: catalogue, customers, subscriptions, every payment with
        its refunds, credit notes, disputes, invoices with line items,
        usage records and the event log. Each record has the same shape
        its ``GET`` route returns, and ``billkit_export_version`` names
        the shape.

        It is ``application/json`` streamed inline, with no redirect, and it
        can be large, so write it to a file rather than holding it in
        memory. Test and live data export separately: you get whichever
        mode the calling key belongs to. Nothing is changed, but the
        access is recorded in your audit log.
        """
        return self._t.request_bytes("GET", "/v1/tenant/export")

    def rotate_provider_credential(
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
        return self._t.request(
            "POST",
            "/v1/tenant/provider_credential",
            json_body=body,
            idempotency_key=idempotency_key,
        )


class Coupons:
    """Create, validate, and page through promotional codes."""

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
        return self._t.request("GET", f"/v1/coupons/{_p(coupon_id)}")

    def update(
        self,
        coupon_id: str,
        *,
        active: bool | None = None,
        max_redemptions: int | _Unset | None = _UNSET,
        redeem_by: int | _Unset | None = _UNSET,
        applies_to_price_ids: list[str] | _Unset | None = _UNSET,
        min_amount_cents: int | _Unset | None = _UNSET,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Patch a coupon's limits, or withdraw it with ``active=False``.

        A withdrawn code is refused at checkout while the coupon stays
        readable and discounts already applied keep working out, which
        is why there is no delete: a redeemed coupon is part of what a
        customer was charged. ``active=True`` brings the campaign back.

        ``max_redemptions=None`` passed explicitly removes the redemption
        cap, ``redeem_by=None`` removes the expiry,
        ``applies_to_price_ids=None`` lifts the price restriction and
        ``min_amount_cents=None`` lifts the minimum (each sent as a JSON
        null); omit any of them to leave it alone.
        """
        body = _drop_none({"active": active})
        _put_clearable(
            body,
            max_redemptions=max_redemptions,
            redeem_by=redeem_by,
            applies_to_price_ids=applies_to_price_ids,
            min_amount_cents=min_amount_cents,
        )
        return self._t.request(
            "POST",
            f"/v1/coupons/{_p(coupon_id)}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def validate(
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
        return self._t.request("POST", "/v1/coupons/validate", json_body=body)

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "GET",
            "/v1/coupons",
            params=_list_params(limit=limit, starting_after=starting_after),
        )

    def iter(self, *, page_size: int | None = None) -> Iterator[dict[str, Any]]:
        return paginate(self.list, page_size=page_size)


class TaxRates:
    """Create, update, and page through per-country VAT rates."""

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
        return self._t.request("GET", f"/v1/tax_rates/{_p(tax_rate_id)}")

    def update(
        self,
        tax_rate_id: str,
        *,
        rate_basis_points: int | None = None,
        display_name: str | _Unset | None = _UNSET,
        inclusive: bool | None = None,
        active: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Correct a rate, retire it with ``active=False``, or bring one back.

        Retiring is how you stop charging VAT in a country. The rate
        stays readable, because an invoice records the percentage it
        charged and you have to be able to point at the rate that
        produced it, which is why there is no delete.

        ``display_name=None`` passed explicitly **clears** the display
        name (sent as a JSON null); omit it to leave it alone.
        """
        body = _drop_none(
            {
                "rate_basis_points": rate_basis_points,
                "inclusive": inclusive,
                "active": active,
            }
        )
        _put_clearable(body, display_name=display_name)
        return self._t.request(
            "POST",
            f"/v1/tax_rates/{_p(tax_rate_id)}",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
    ) -> dict[str, Any]:
        return self._t.request(
            "GET",
            "/v1/tax_rates",
            params=_list_params(limit=limit, starting_after=starting_after),
        )

    def iter(self, *, page_size: int | None = None) -> Iterator[dict[str, Any]]:
        return paginate(self.list, page_size=page_size)


class Invoices:
    """Read-only access to generated invoices.

    Invoices are produced by the billing pipeline; tenants don't create
    them directly. :meth:`retrieve_pdf` hands back the rendered bytes: a
    blob-backed deployment streams them inline and an S3-backed one
    answers 302 to a presigned URL, which the transport follows for you,
    so both look identical from here.
    """

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def retrieve(self, invoice_id: str, *, expand: list[str] | None = None) -> dict[str, Any]:
        """Fetch one invoice, line items included.

        Expandable: ``customer``.
        """
        return self._t.request(
            "GET", f"/v1/invoices/{_p(invoice_id)}", params=_expand_params(expand)
        )

    def retrieve_pdf(self, invoice_id: str) -> bytes:
        """Download the rendered invoice PDF as raw bytes.

        .. code-block:: python

            pdf = client.invoices.retrieve_pdf("inv_123")
            Path("invoice.pdf").write_bytes(pdf)

        Blob-backed deployments stream the bytes inline; S3-backed ones
        answer ``302`` to a presigned URL, which the transport follows
        under the SDK's own timeout and retry policy — so both storage
        adapters look identical from here.

        Deployments with ``INVOICE_PDF_ENABLED=false`` never render one
        and answer ``501 rendering_pending``, which surfaces as a
        :class:`~billkit.ServerError` whose ``code`` is
        ``"rendering_pending"``; :meth:`retrieve` still returns the
        structured invoice for tenants who render their own.
        """
        return self._t.request_bytes("GET", f"/v1/invoices/{_p(invoice_id)}/pdf")

    def send_email(self, invoice_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Send the customer their invoice again.

        The same tenant-branded "your invoice is ready" email, with a
        fresh portal link, because the one in the original may have
        expired and re-sending a dead link is worse than not re-sending.

        It goes to the address captured **on the invoice**, not the
        customer's current one: this is a copy of a document that was
        issued to somebody, and quietly redirecting it would make the
        resend a different act from the original send. An invoice with no
        address on file raises :class:`~billkit.InvalidRequestError`
        rather than reporting a send that did not happen.
        """
        return self._t.request(
            "POST",
            f"/v1/invoices/{_p(invoice_id)}/email",
            idempotency_key=idempotency_key,
        )

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        customer_id: str | None = None,
        subscription_id: str | None = None,
        payment_id: str | None = None,
        status: str | None = None,
        expand: list[str] | None = None,
    ) -> dict[str, Any]:
        """List invoices, newest first. Line items are left out here.

        ``customer_id``, ``subscription_id`` and ``payment_id`` each
        narrow to one, which is how you ask "show me this customer's
        invoices" or "which invoice did this charge produce" without
        paging the whole account. ``status`` takes one of ``draft``,
        ``open``, ``paid``, ``void``, ``uncollectible``.

        Expandable: ``customer``.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        params.update(
            _drop_none(
                {
                    "customer_id": customer_id,
                    "subscription_id": subscription_id,
                    "payment_id": payment_id,
                    "status": status,
                }
            )
        )
        params.update(_expand_params(expand) or {})
        return self._t.request("GET", "/v1/invoices", params=params)

    def iter(
        self,
        *,
        page_size: int | None = None,
        customer_id: str | None = None,
        subscription_id: str | None = None,
        payment_id: str | None = None,
        status: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()``; filters are carried on each."""
        return paginate(
            self.list,
            page_size=page_size,
            customer_id=customer_id,
            subscription_id=subscription_id,
            payment_id=payment_id,
            status=status,
        )

    def void(
        self,
        invoice_id: str,
        *,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Void an invoice: state that the sale was never owed.

        The invoice keeps its number and stays readable — a gapless
        series cannot lose a row — and stops being a receivable. Use it
        for an invoice that should not have been issued.

        A **paid** invoice is refused with :class:`ConflictError` whose
        ``code`` is ``invoice_not_voidable``. That is deliberate: once
        the money has moved, "never owed" is false, and the document that
        reverses a real sale is a credit note — refund the payment and
        one is issued when the refund settles.

        Idempotent: voiding an already-void invoice returns it unchanged.

        ``reason`` is recorded on the audit row only, never on the
        document.
        """
        return self._t.request(
            "POST",
            f"/v1/invoices/{_p(invoice_id)}/void",
            json_body=_drop_none({"reason": reason}),
            idempotency_key=idempotency_key,
        )


class CreditNotes:
    """Read-only access to credit notes — the documents that reverse an
    issued invoice.

    There is no create. A credit note is issued for you when a refund
    settles, never on request, so a numbered legal record is only minted
    once the money has actually moved. A refund still pending, a refund
    that fails, and a refund of a one-off charge that was never invoiced
    all produce none.
    """

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def retrieve(self, credit_note_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/credit_notes/{_p(credit_note_id)}")

    def retrieve_pdf(self, credit_note_id: str) -> bytes:
        """Download the rendered credit note PDF as raw bytes.

        .. code-block:: python

            pdf = client.credit_notes.retrieve_pdf("cn_123")
            Path("credit-note.pdf").write_bytes(pdf)

        Blob-backed deployments stream the bytes inline; S3-backed ones
        answer ``302`` to a presigned URL, which the transport follows
        under the SDK's own timeout and retry policy — so both storage
        adapters look identical from here.

        Deployments with ``INVOICE_PDF_ENABLED=false`` never render one
        and answer ``501 rendering_pending``, which surfaces as a
        :class:`~billkit.ServerError` whose ``code`` is
        ``"rendering_pending"``; :meth:`retrieve` still returns the
        structured credit note for tenants who render their own.
        """
        return self._t.request_bytes("GET", f"/v1/credit_notes/{_p(credit_note_id)}/pdf")

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        invoice_id: str | None = None,
        customer_id: str | None = None,
    ) -> dict[str, Any]:
        """One page of credit notes, newest first.

        ``invoice_id`` answers "was this sale credited, and by how much",
        which is the question when reconciling a single invoice;
        ``customer_id`` answers it for everything credited to one buyer.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        for key, value in (("invoice_id", invoice_id), ("customer_id", customer_id)):
            if value is not None:
                params[key] = value
        return self._t.request("GET", "/v1/credit_notes", params=params)

    def iter(
        self,
        *,
        page_size: int | None = None,
        invoice_id: str | None = None,
        customer_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        return paginate(
            self.list, page_size=page_size, invoice_id=invoice_id, customer_id=customer_id
        )


class AuditLogs:
    """Read-only access to the per-tenant audit log."""

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def retrieve(self, audit_log_id: str) -> dict[str, Any]:
        return self._t.request("GET", f"/v1/audit_logs/{_p(audit_log_id)}")

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        """List audit-log entries, newest first, optionally filtered.

        All four filters match exactly and combine. ``resource_type``
        narrows to a kind (``"customer"``, ``"price"``); ``resource_id``
        narrows to one row, which is the "everything that ever happened
        to this customer" question an audit log mostly exists for. Pair
        them or pass ``resource_id`` alone — ids are already unique.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        for key, value in (
            ("action", action),
            ("resource_type", resource_type),
            ("resource_id", resource_id),
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
        resource_id: str | None = None,
        actor_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()``; filters are forwarded
        unchanged so ``action="customer.created"`` etc. work."""
        return paginate(
            self.list,
            page_size=page_size,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_id=actor_id,
        )


class Payments:
    """Read-only access to the payment ledger.

    Payments are written by the billing pipeline (checkout, renewal,
    reauthorize). Use this resource to inspect attempts and their
    Mollie-side metadata; refunds and disputes are separate flows.
    """

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def retrieve(self, payment_id: str, *, expand: list[str] | None = None) -> dict[str, Any]:
        """Fetch one subscription payment.

        Expandable: ``customer``, ``subscription``, ``refund_eligibility``.
        ``refund_eligibility`` is retrieve-only (:meth:`list` refuses it)
        and attaches ``{"object": "refund_eligibility", "eligible",
        "amount_cents", "currency", "days_remaining", "window_ends_at",
        "reason"}``: whether a refund of the remaining balance would
        succeed now, applying the refund window and the price's refund
        policy, which ``amount_refundable_cents`` does not. When it would
        not, ``reason`` is ``not_paid``, ``unrefundable_type``,
        ``window_expired``, ``fully_refunded``, ``disputed``,
        ``operation_pending`` or ``plan_change_pending`` (a plan change is
        settling: the full balance cannot be refunded yet, a partial refund
        still can); treat any other value as "not refundable".
        """
        return self._t.request(
            "GET", f"/v1/payments/{_p(payment_id)}", params=_expand_params(expand)
        )

    def retrieve_provider(self, payment_id: str) -> dict[str, Any]:
        """Fetch the provider's own record of this charge, live.

        Reads Mollie at request time rather than a stored copy, so it
        carries what BillKit deliberately does not keep: the card BIN, the
        iDEAL bank, the provider's own status string. Reading live means
        it can fail: a provider outage, a credential that no longer
        authorises the profile, or a charge old enough to have aged out
        all answer ``200`` with ``available: False`` and a short reason,
        so render the rest of the page regardless.
        """
        return self._t.request("GET", f"/v1/payments/{_p(payment_id)}/provider")

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
        customer_id: str | None = None,
        expand: list[str] | None = None,
    ) -> dict[str, Any]:
        """List subscription payments, newest first.

        ``customer_id`` narrows to one customer. Failed and pending
        attempts are listed alongside successful ones, so check ``status``
        before treating a row as revenue. Mandate verifications are never
        listed, because nothing was sold, and one-off charges live under
        ``one_shot_payments``.

        Expandable: ``customer``, ``subscription``.
        """
        params = _list_params(limit=limit, starting_after=starting_after)
        params.update(_drop_none({"customer_id": customer_id}))
        params.update(_expand_params(expand) or {})
        return self._t.request("GET", "/v1/payments", params=params)

    def iter(
        self, *, page_size: int | None = None, customer_id: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()``; ``customer_id`` is carried on each."""
        return paginate(self.list, page_size=page_size, customer_id=customer_id)


class BillingPortalSessions:
    """Mint and revoke customer-facing billing-portal sessions.

    Each session token is scoped to a single subscription with a
    sliding 30-minute idle window and 2-hour hard cap. The token is
    returned **once** on mint; the response also includes the URL the
    tenant embeds in their app.
    """

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def create(
        self,
        *,
        subscription_id: str,
        return_url: str,
        deliver_email: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Mint a portal session for one subscription.

        ``deliver_email=True`` also emails the link to the subscription's
        customer, at the address on their record, as a tenant-branded
        message. It defaults to off: without it you distribute the
        returned ``url`` yourself.
        """
        body = _drop_none(
            {
                "subscription_id": subscription_id,
                "return_url": return_url,
                "deliver_email": deliver_email,
            }
        )
        return self._t.request(
            "POST",
            "/v1/billing_portal/sessions",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def revoke(self, session_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Kill an in-the-wild portal session. Idempotent."""
        return self._t.request(
            "POST",
            f"/v1/billing_portal/sessions/{_p(session_id)}/revoke",
            idempotency_key=idempotency_key,
        )


class ApiKeys:
    """Issue, inspect and revoke API keys.

    A key is issued in the same mode as the key that created it, so a test
    key can only mint test keys, and it can never grant scopes it does not
    hold itself. The secret is returned **once**, on :meth:`create`; every
    later read carries only the prefix.
    """

    def __init__(self, transport: _SyncRequester) -> None:
        self._t = transport

    def create(
        self,
        *,
        label: str | None = None,
        scopes: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Issue a new key.

        The response's ``secret`` is the only time the full key exists
        outside your own storage, so record it now; it is never retrievable
        again. ``scopes`` narrows what the key may do, which is the point
        of minting one per integration rather than sharing a single key;
        omit it and the new key inherits the calling key's own. An
        unrecognised scope is rejected at creation rather than failing
        later on every call.
        """
        body = _drop_none({"label": label, "scopes": scopes})
        return self._t.request(
            "POST", "/v1/api_keys", json_body=body, idempotency_key=idempotency_key
        )

    def retrieve(self, api_key_id: str) -> dict[str, Any]:
        """One key's metadata: prefix, label, scopes, ``revoked_at``.

        The key itself is never returned. ``last_used_at`` is the useful
        field, since it tells you whether a key is still in service before
        you revoke it.
        """
        return self._t.request("GET", f"/v1/api_keys/{_p(api_key_id)}")

    def revoke(self, api_key_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Revoke a key so it stops working.

        Immediate and irreversible; issue a new key instead. Revoking an
        already-revoked key returns it unchanged, so a retry is safe, and
        a key may revoke itself, which is what you want when the leaked
        key is the one you are calling with.
        """
        return self._t.request(
            "POST",
            f"/v1/api_keys/{_p(api_key_id)}/revoke",
            idempotency_key=idempotency_key,
        )

    def list(
        self,
        *,
        limit: int | None = None,
        starting_after: str | None = None,
    ) -> dict[str, Any]:
        """List your API keys, newest first.

        Only keys in the calling key's mode are listed. Revoked ones are
        included, so check ``revoked_at``.
        """
        return self._t.request(
            "GET",
            "/v1/api_keys",
            params=_list_params(limit=limit, starting_after=starting_after),
        )

    def iter(self, *, page_size: int | None = None) -> Iterator[dict[str, Any]]:
        """Walk every page of ``list()`` and yield each key."""
        return paginate(self.list, page_size=page_size)
