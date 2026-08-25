"""Auto-pagination helpers for list endpoints.

The BillKit API returns Stripe-shape envelopes::

    { "object": "list", "data": [...], "has_more": bool }

Cursor pagination is forward-only via the last item's ``id`` as
``starting_after``. The helpers below walk every page until
``has_more`` is false and yield each row. Callers iterate::

    for customer in client.customers.iter():
        ...

The sync flavour returns a generator; the async one returns an
async iterator. Both forward keyword arguments to ``list()`` so
filters (e.g. ``status``) flow through unchanged.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any


def paginate(
    list_fn: Callable[..., dict[str, Any]],
    *,
    page_size: int | None = None,
    **filters: Any,
) -> Iterator[dict[str, Any]]:
    """Walk every page of ``list_fn`` and yield each row.

    ``page_size`` maps to the API's ``limit`` parameter; leaving it
    ``None`` lets the server pick its default (10 today). Other
    ``filters`` are passed through to ``list_fn`` on every page.
    """
    cursor: str | None = None
    while True:
        page = list_fn(limit=page_size, starting_after=cursor, **filters)
        items = page.get("data") or []
        yield from items
        # Three terminators, in priority order:
        #   1. ``has_more`` is the server's authoritative signal, and the
        #      common case.
        #   2. Empty data with ``has_more=True`` shouldn't happen per
        #      the API contract, but if a future server bug or proxy
        #      misbehaviour produced it the iterator would loop
        #      forever. Belt-and-suspenders: bail.
        #   3. ``id``-less items have no cursor to advance with, so the
        #      same defensive reasoning applies. ``items[-1].get("id")``
        #      will only return ``None`` if a row drops its ``id``,
        #      which the schema doesn't allow today.
        if not page.get("has_more"):
            return
        if not items:
            return
        cursor = items[-1].get("id")
        if cursor is None:
            return


async def aiterate(
    list_fn: Callable[..., Awaitable[dict[str, Any]]],
    *,
    page_size: int | None = None,
    **filters: Any,
) -> AsyncIterator[dict[str, Any]]:
    """Async sibling of :func:`paginate`.

    Termination logic is identical; see :func:`paginate` for the three
    bail-out cases."""
    cursor: str | None = None
    while True:
        page = await list_fn(limit=page_size, starting_after=cursor, **filters)
        items = page.get("data") or []
        for item in items:
            yield item
        if not page.get("has_more"):
            return
        if not items:
            return
        cursor = items[-1].get("id")
        if cursor is None:
            return
