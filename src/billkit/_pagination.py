"""Auto-pagination behind every resource's ``iter()``.

The API's lists are Stripe-shaped::

    {"object": "list", "data": [...], "has_more": bool}

Cursor pagination is forward-only, via the last item's ``id`` as
``starting_after``. Each ``iter()`` hands these helpers a ``fetch``
callable that requests one page from a cursor, with every filter and
per-request option the caller gave ``iter()`` already bound, so the
filters reach every page and not just the first::

    for customer in client.customers.iter(provisional=False, expand=["stats"]):
        ...
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import TypeVar

from billkit._models import ListObject

_T = TypeVar("_T")


def _next_cursor(page: ListObject[_T]) -> str | None:
    """The cursor for the page after ``page``, or ``None`` to stop.

    Three terminators, in priority order:

    1. ``has_more`` false: the server's authoritative signal, and the
       common case.
    2. An empty page that claims ``has_more``. The contract does not allow
       it, but a server bug or a misbehaving proxy producing it would
       otherwise loop forever.
    3. A last item without an ``id``: there is no cursor to advance with.
    """
    if not page.has_more or not page.data:
        return None
    last = page.data[-1]
    cursor = getattr(last, "id", None)
    if cursor is None and isinstance(last, dict):
        cursor = last.get("id")
    return cursor if isinstance(cursor, str) and cursor else None


def paginate(
    fetch: Callable[[str | None], ListObject[_T]],
    *,
    starting_after: str | None = None,
) -> Iterator[_T]:
    """Walk every page ``fetch`` returns, from ``starting_after``, yielding items."""
    cursor = starting_after
    while True:
        page = fetch(cursor)
        yield from page.data
        next_cursor = _next_cursor(page)
        if next_cursor is None:
            return
        cursor = next_cursor


async def aiterate(
    fetch: Callable[[str | None], Awaitable[ListObject[_T]]],
    *,
    starting_after: str | None = None,
) -> AsyncIterator[_T]:
    """Async sibling of :func:`paginate`, with the same terminators."""
    cursor = starting_after
    while True:
        page = await fetch(cursor)
        for item in page.data:
            yield item
        next_cursor = _next_cursor(page)
        if next_cursor is None:
            return
        cursor = next_cursor
