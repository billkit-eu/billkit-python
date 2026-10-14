"""Typed shapes for the structured request parameters.

Plain ``dict`` literals satisfy them, so nothing changes at the call site;
a type checker just catches a misspelt or retired key before the API does.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, NotRequired, TypedDict

__all__ = ["PriceTierParam"]


class PriceTierParam(TypedDict):
    """One band of a tiered price, as ``prices.create(tiers=[...])`` takes it.

    ``up_to`` is the band's inclusive upper bound; the last band must be
    ``"inf"``. Set a per-unit rate with ``unit_amount_cents`` (whole minor
    units) or ``unit_amount_decimal`` (finer than a minor unit, as a
    ``str``, ``int`` or ``Decimal``, never a ``float``), and/or a
    ``flat_amount_cents`` charged once for reaching the band.

    The 0.x examples spelled these ``unit_amount`` and ``flat_amount``; the
    1.0 API names every money field ``*_cents`` and refuses the old keys.
    """

    up_to: int | Literal["inf"]
    unit_amount_cents: NotRequired[int]
    unit_amount_decimal: NotRequired[str | int | Decimal]
    flat_amount_cents: NotRequired[int]
