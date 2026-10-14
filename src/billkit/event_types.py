"""Every event type BillKit can deliver, as constants.

Generated from the API's catalogue (``api/billkit/core/events.py``) and
checked against it in both directions by
``api/tests/test_sdk_event_constants.py``, so a type the API adds or drops
fails the monorepo build until this copy follows. Use them to subscribe an
endpoint and to route a delivery without a typo that would never match::

    from billkit import EventType

    if event["type"] == EventType.INVOICE_PAID:
        ...

``EventType`` is a :class:`~enum.StrEnum`, so a member compares equal to the
plain wire string and can be passed anywhere a ``str`` is expected.

The list describes the API this SDK version was built against, nothing
more. A newer API can deliver a type this list does not name, so a webhook
handler must treat an unknown ``type`` as "ignore", never as an error.
Nothing in the SDK rejects one: :meth:`billkit.WebhookSignature.verify`
does not look at ``type`` at all. Use :func:`is_known_event_type` rather
than ``EventType(value)``, which raises on a type it does not know.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class EventType(StrEnum):
    """Every event type the API can deliver."""

    # Checkout
    CHECKOUT_SESSION_COMPLETED = "checkout.session.completed"
    CHECKOUT_SESSION_EXPIRED = "checkout.session.expired"

    # Customer
    CUSTOMER_CREATED = "customer.created"
    CUSTOMER_UPDATED = "customer.updated"
    CUSTOMER_DELETED = "customer.deleted"

    # Subscription lifecycle
    SUBSCRIPTION_CREATED = "subscription.created"
    SUBSCRIPTION_UPDATED = "subscription.updated"
    SUBSCRIPTION_CANCELED = "subscription.canceled"
    SUBSCRIPTION_PAST_DUE = "subscription.past_due"
    SUBSCRIPTION_PAUSED = "subscription.paused"
    SUBSCRIPTION_RESUMED = "subscription.resumed"
    SUBSCRIPTION_REACTIVATED = "subscription.reactivated"
    SUBSCRIPTION_PLAN_CHANGED = "subscription.plan_changed"
    SUBSCRIPTION_TRIAL_STARTED = "subscription.trial_started"
    SUBSCRIPTION_TRIAL_WILL_END = "subscription.trial_will_end"
    SUBSCRIPTION_TRIAL_ENDED = "subscription.trial_ended"
    SUBSCRIPTION_PAYMENT_METHOD_UPDATED = "subscription.payment_method_updated"
    SUBSCRIPTION_COUPON_APPLIED = "subscription.coupon_applied"
    SUBSCRIPTION_COUPON_EXPIRED = "subscription.coupon_expired"

    # Payments
    PAYMENT_SUCCEEDED = "payment.succeeded"
    PAYMENT_FAILED = "payment.failed"
    ONE_SHOT_PAYMENT_SUCCEEDED = "one_shot_payment.succeeded"
    ONE_SHOT_PAYMENT_FAILED = "one_shot_payment.failed"
    ONE_SHOT_PAYMENT_REFUNDED = "one_shot_payment.refunded"

    # Refunds and disputes
    REFUND_CREATED = "refund.created"
    REFUND_SUCCEEDED = "refund.succeeded"
    REFUND_FAILED = "refund.failed"
    DISPUTE_CREATED = "dispute.created"
    DISPUTE_CLOSED = "dispute.closed"

    # Documents
    INVOICE_CREATED = "invoice.created"
    INVOICE_UPCOMING = "invoice.upcoming"
    INVOICE_PAID = "invoice.paid"
    INVOICE_PAYMENT_FAILED = "invoice.payment_failed"
    INVOICE_VOIDED = "invoice.voided"
    INVOICE_MARKED_UNCOLLECTIBLE = "invoice.marked_uncollectible"
    CREDIT_NOTE_CREATED = "credit_note.created"

    # Catalogue
    PRODUCT_CREATED = "product.created"
    PRODUCT_UPDATED = "product.updated"
    PRODUCT_ARCHIVED = "product.archived"
    PRICE_CREATED = "price.created"
    PRICE_UPDATED = "price.updated"
    PRICE_ARCHIVED = "price.archived"
    COUPON_CREATED = "coupon.created"
    COUPON_UPDATED = "coupon.updated"

    # Endpoint management
    WEBHOOK_ENDPOINT_CREATED = "webhook_endpoint.created"
    WEBHOOK_ENDPOINT_UPDATED = "webhook_endpoint.updated"
    WEBHOOK_ENDPOINT_DELETED = "webhook_endpoint.deleted"

    # Operational
    DUNNING_EMAIL_REQUIRED = "dunning.email_required"


#: Every value of :class:`EventType`, as plain wire strings.
EVENT_TYPES: Final[frozenset[str]] = frozenset(e.value for e in EventType)


def is_known_event_type(value: str) -> bool:
    """Whether this SDK version knows ``value``.

    ``False`` is not an error: it usually means the API is newer than the SDK.
    """
    return value in EVENT_TYPES
