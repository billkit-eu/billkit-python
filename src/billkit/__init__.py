"""Official Python SDK for BillKit.

Quick start
-----------

.. code-block:: python

    from billkit import BillKit

    client = BillKit(api_key="bk_test_...")
    customer = client.customers.create(email="ada@example.com", name="Ada Lovelace")
    product = client.products.create(name="Pro")
    price = client.prices.create(
        product_id=product.id,
        amount_cents=999,
        currency="EUR",
        interval="month",
    )
    print(customer.id)  # cus_...
    print(price["id"])  # price_..., the mapping style works too

Async variant
-------------

.. code-block:: python

    from billkit import AsyncBillKit

    async with AsyncBillKit(api_key="bk_test_...") as client:
        customer = await client.customers.create(email="ada@example.com")

Webhook verification
--------------------

.. code-block:: python

    from billkit import WebhookSignature, WebhookVerificationError, types

    try:
        event = WebhookSignature.verify(
            payload=request.body,
            signature_header=request.headers["BillKit-Signature"],
            secret="bkwhsec_...",
        )
    except WebhookVerificationError:
        return Response(status_code=400)

    if isinstance(event, types.SubscriptionUpdatedEvent):
        sync_subscription(event.data)  # a typed billkit.types.Subscription

Logging
-------

The SDK is silent by default: it attaches a ``NullHandler`` to a single
``"billkit"`` logger and never configures anything else, so it can't
hijack your application's log setup. Opt in when you need to see the
request/retry lifecycle:

.. code-block:: python

    import logging

    logging.basicConfig()
    logging.getLogger("billkit").setLevel(logging.DEBUG)

API keys, request/response bodies and query strings are never logged.
See :mod:`billkit._logging` for the full policy.
"""

from billkit import types
from billkit._client import AsyncBillKit, BillKit
from billkit._errors import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BillKitError,
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    PaymentError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    WebhookVerificationError,
)
from billkit._logging import logger
from billkit._models import BillKitObject, ListObject
from billkit._options import NOT_GIVEN, NotGiven
from billkit._params import PriceTierParam
from billkit._retry import DEFAULT_RETRY_POLICY, RetryPolicy
from billkit._transport import DEFAULT_TIMEOUT
from billkit._version import __version__
from billkit._webhooks import WebhookSignature
from billkit.event_types import EVENT_TYPES, EventType, is_known_event_type
from billkit.types import Event

__all__ = [
    "DEFAULT_RETRY_POLICY",
    "DEFAULT_TIMEOUT",
    "EVENT_TYPES",
    "NOT_GIVEN",
    "APIConnectionError",
    "APIError",
    "APITimeoutError",
    "AsyncBillKit",
    "AuthenticationError",
    "BillKit",
    "BillKitError",
    "BillKitObject",
    "ConflictError",
    "Event",
    "EventType",
    "InvalidRequestError",
    "ListObject",
    "NotFoundError",
    "NotGiven",
    "PaymentError",
    "PermissionDeniedError",
    "PriceTierParam",
    "RateLimitError",
    "RetryPolicy",
    "ServerError",
    "WebhookSignature",
    "WebhookVerificationError",
    "__version__",
    "is_known_event_type",
    "logger",
    "types",
]
