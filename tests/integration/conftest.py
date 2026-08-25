"""Live-API harness for the python SDK integration suite.

Talks to a real BillKit API booted with ``BILLKIT_E2E_TEST_LOGIN=1``
(see ``sdk/integration/boot-api.sh``). The whole package is skipped
unless ``BILLKIT_INTEGRATION_BASE_URL`` is set, so a laptop without a
running stack keeps ``make all-tests`` green.

Two env-gated API surfaces do the heavy lifting:

* ``POST /v1/console/auth/_test/login`` provisions a *fresh tenant*
  per unseen email and hands back a wildcard ``api_key`` plus the
  tenant's ``mollie_route_id``. Every run uses a unique email, so a
  suite never inherits another run's rows and list assertions stay
  meaningful.
* ``POST /v1/console/auth/_test/mollie/*`` drives the in-process fake
  Mollie provider, which is what makes the money-path scenarios
  deterministic without touching real Mollie.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

import httpx
import pytest

from billkit import BillKit

BASE_URL = os.environ.get("BILLKIT_INTEGRATION_BASE_URL", "")

pytestmark = pytest.mark.skipif(
    not BASE_URL,
    reason="Set BILLKIT_INTEGRATION_BASE_URL to run the SDK integration suite.",
)


@dataclass(frozen=True)
class ITTenant:
    """Credentials for one provisioned tenant.

    Named ``ITTenant`` rather than ``TestTenant`` so pytest does not try to
    collect it as a test class (anything matching ``Test*`` is a collection
    candidate, and a dataclass with ``__init__`` emits a warning).
    """

    api_key: str
    tenant_id: str
    #: Path segment of ``/internal/webhooks/mollie/{route_id}``.
    mollie_route_id: str
    session_token: str


def provision_tenant(label: str = "py-sdk-it") -> ITTenant:
    """Provision a brand-new tenant and return its credentials.

    The email is randomised per call precisely so each suite gets its own
    tenant. List assertions ("exactly the 7 products I created") are only
    stable under that isolation.
    """
    email = f"{label}-{uuid.uuid4()}@sdk-it.example.com"
    resp = httpx.post(
        f"{BASE_URL}/v1/console/auth/_test/login",
        json={"email": email, "mode": "test", "tenant_name": f"Py SDK IT {label}"},
        timeout=30.0,
    )
    if resp.status_code == 404:
        raise RuntimeError(
            "test-login backdoor returned 404; boot the API with "
            "BILLKIT_E2E_TEST_LOGIN=1 (see sdk/integration/boot-api.sh)."
        )
    resp.raise_for_status()
    body = resp.json()
    return ITTenant(
        api_key=body["api_key"],
        tenant_id=body["operator"]["tenant_id"],
        mollie_route_id=body["mollie_route_id"],
        session_token=body["session_token"],
    )


class MollieControl:
    """Drive the in-process fake Mollie provider."""

    @staticmethod
    def payment_id_from_checkout_url(url: str) -> str:
        """Recover the provider payment id from a checkout session's URL.

        The API never returns ``tr_...`` directly, but the fake encodes it in
        the redirect URL, which keeps this per-checkout and free of shared
        state.
        """
        candidate = url.rsplit("/", 1)[-1]
        if not candidate.startswith("tr_"):
            raise AssertionError(f"Expected a Mollie payment id in checkout URL, got: {url}")
        return candidate

    @staticmethod
    def settle(payment_id: str, status: str = "paid") -> None:
        resp = httpx.post(
            f"{BASE_URL}/v1/console/auth/_test/mollie/settle",
            json={"payment_id": payment_id, "status": status},
            timeout=30.0,
        )
        resp.raise_for_status()

    @staticmethod
    def chargeback(payment_id: str, amount_value: str, reason: str | None = None) -> None:
        resp = httpx.post(
            f"{BASE_URL}/v1/console/auth/_test/mollie/chargeback",
            json={"payment_id": payment_id, "amount_value": amount_value, "reason": reason},
            timeout=30.0,
        )
        resp.raise_for_status()

    @staticmethod
    def refund_status(refund_id: str, status: str = "refunded") -> None:
        resp = httpx.post(
            f"{BASE_URL}/v1/console/auth/_test/mollie/refund_status",
            json={"refund_id": refund_id, "status": status},
            timeout=30.0,
        )
        resp.raise_for_status()


def deliver_mollie_webhook(route_id: str, provider_payment_id: str) -> None:
    """Post the provider webhook the way Mollie does, form-encoded ``id=tr_...``.

    The API ignores the body's claims about state and re-fetches the payment
    from the provider, so this call is only a *nudge*; :meth:`MollieControl.settle`
    is what actually decides the outcome. Driving them in that order is what
    makes the money-path specs deterministic.
    """
    resp = httpx.post(
        f"{BASE_URL}/internal/webhooks/mollie/{route_id}",
        content=f"id={provider_payment_id}",
        headers={"content-type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    resp.raise_for_status()


def mint_scoped_key(tenant: ITTenant, scopes: list[str]) -> str:
    """Mint an API key with a restricted scope set, for the scope-denial spec."""
    resp = httpx.post(
        f"{BASE_URL}/v1/api_keys",
        json={"label": f"scoped-{uuid.uuid4().hex[:8]}", "scopes": scopes},
        headers={"authorization": f"Bearer {tenant.api_key}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return str(resp.json()["secret"])


def idem_key() -> str:
    return f"it-{uuid.uuid4()}"


@pytest.fixture(scope="module")
def tenant() -> ITTenant:
    return provision_tenant()


@pytest.fixture(scope="module")
def client(tenant: ITTenant) -> BillKit:
    return BillKit(api_key=tenant.api_key, base_url=BASE_URL)
