"""Official Python SDK for BillKit.

Quick start
-----------

.. code-block:: python

    from billkit import BillKit

    client = BillKit(api_key="sk_test_...")
    customer = client.customers.create(email="ada@example.com", name="Ada Lovelace")
    product = client.products.create(name="Pro")
    price = client.prices.create(
        product_id=product["id"],
        amount_cents=999,
        currency="EUR",
        interval="month",
    )
    print(customer["id"])  # cus_...
    print(price["id"])  # price_...

Async variant
-------------

.. code-block:: python

    from billkit import AsyncBillKit

    async with AsyncBillKit(api_key="sk_test_...") as client:
        customer = await client.customers.create(email="ada@example.com")

Webhook verification
--------------------

.. code-block:: python

    from billkit import WebhookSignature, WebhookVerificationError

    try:
        event = WebhookSignature.verify(
            payload=request.body,
            signature_header=request.headers["BillKit-Signature"],
            secret="whsec_...",
        )
    except WebhookVerificationError:
        return Response(status_code=400)

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

from billkit._client import AsyncBillKit, BillKit
from billkit._errors import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    BillKitError,
    ConflictError,
    InvalidRequestError,
    PermissionError,
    RateLimitError,
    ResourceMissingError,
    ServerError,
)
from billkit._logging import logger
from billkit._retry import DEFAULT_RETRY_POLICY, RetryPolicy
from billkit._version import __version__
from billkit._webhooks import WebhookSignature, WebhookVerificationError

__all__ = [
    "DEFAULT_RETRY_POLICY",
    "APIConnectionError",
    "APIError",
    "AsyncBillKit",
    "AuthenticationError",
    "BillKit",
    "BillKitError",
    "ConflictError",
    "InvalidRequestError",
    "PermissionError",
    "RateLimitError",
    "ResourceMissingError",
    "RetryPolicy",
    "ServerError",
    "WebhookSignature",
    "WebhookVerificationError",
    "__version__",
    "logger",
]
