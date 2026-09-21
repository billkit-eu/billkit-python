"""The document routes: invoice and credit-note PDFs.

These are the only calls that return bytes rather than JSON, and the only
ones that follow a redirect. Both differences are where the mistakes live:
handing back a JSON-decoded husk instead of a PDF, losing the typed error
when a deployment has no renderer, and — the one that matters — carrying
the API key across to the storage host that the redirect points at.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from billkit import APIConnectionError, AsyncBillKit, BillKit, ServerError

PDF = b"%PDF-1.7\nnot json\n"


@respx.mock
def test_invoice_pdf_returns_raw_bytes(sync_client: BillKit) -> None:
    route = respx.get("https://test.billkit.eu/v1/invoices/inv_1/pdf").mock(
        return_value=httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
    )
    assert sync_client.invoices.retrieve_pdf("inv_1") == PDF
    assert route.call_count == 1


@respx.mock
def test_credit_note_pdf_returns_raw_bytes(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/credit_notes/cn_1/pdf").mock(
        return_value=httpx.Response(200, content=PDF)
    )
    assert sync_client.credit_notes.retrieve_pdf("cn_1") == PDF


@respx.mock
def test_pdf_follows_the_storage_redirect(sync_client: BillKit) -> None:
    """S3-backed deployments answer 302 to a presigned URL."""
    respx.get("https://test.billkit.eu/v1/invoices/inv_1/pdf").mock(
        return_value=httpx.Response(302, headers={"location": "https://s3.test/obj?sig=abc"})
    )
    signed = respx.get("https://s3.test/obj").mock(return_value=httpx.Response(200, content=PDF))
    assert sync_client.invoices.retrieve_pdf("inv_1") == PDF
    assert signed.call_count == 1


@respx.mock
def test_pdf_redirect_does_not_carry_the_api_key(sync_client: BillKit) -> None:
    """The one that matters.

    The presigned URL carries its own credential. Handing the storage host
    BillKit's API key as well would hand a third party a live secret, and
    it would be in their access logs.
    """
    respx.get("https://test.billkit.eu/v1/invoices/inv_1/pdf").mock(
        return_value=httpx.Response(302, headers={"location": "https://s3.test/obj?sig=abc"})
    )
    signed = respx.get("https://s3.test/obj").mock(return_value=httpx.Response(200, content=PDF))

    sync_client.invoices.retrieve_pdf("inv_1")

    sent = {k.lower() for k in signed.calls[0].request.headers}
    assert "authorization" not in sent


@respx.mock
def test_pdf_does_not_follow_a_redirect_forever(sync_client: BillKit) -> None:
    """A storage adapter stuck in a loop must fail, not hang."""
    respx.get("https://test.billkit.eu/v1/invoices/inv_1/pdf").mock(
        return_value=httpx.Response(302, headers={"location": "https://s3.test/a"})
    )
    respx.get("https://s3.test/a").mock(
        return_value=httpx.Response(302, headers={"location": "https://s3.test/a"})
    )
    with pytest.raises(APIConnectionError) as excinfo:
        sync_client.invoices.retrieve_pdf("inv_1")
    assert "redirect" in str(excinfo.value).lower()


@respx.mock
def test_pdf_still_raises_the_typed_error(sync_client: BillKit) -> None:
    """``INVOICE_PDF_ENABLED=false`` answers 501 with the normal envelope.

    An error is an error envelope whichever endpoint produced it, so the
    document routes must raise the same typed error as everything else
    rather than hand back a body that is not a PDF.
    """
    respx.get("https://test.billkit.eu/v1/invoices/inv_1/pdf").mock(
        return_value=httpx.Response(
            501,
            json={
                "error": {
                    "type": "api_error",
                    "code": "rendering_pending",
                    "message": "off",
                }
            },
        )
    )
    with pytest.raises(ServerError) as excinfo:
        sync_client.invoices.retrieve_pdf("inv_1")
    assert excinfo.value.code == "rendering_pending"
    assert excinfo.value.status_code == 501


@respx.mock
def test_pdf_sends_no_idempotency_key(sync_client: BillKit) -> None:
    """A GET is not a mutation; a key on one would be noise in the ledger."""
    route = respx.get("https://test.billkit.eu/v1/invoices/inv_1/pdf").mock(
        return_value=httpx.Response(200, content=PDF)
    )
    sync_client.invoices.retrieve_pdf("inv_1")
    assert "idempotency-key" not in {k.lower() for k in route.calls[0].request.headers}


# ── the async transport is a separate hand-written copy ───────────────
#
# `resources.py` generates its sync half from its async one, but
# `_transport.py` does not: `AsyncTransport` and `SyncTransport` are two
# hand-written classes, so `request_bytes` exists twice and the bytes path
# and the redirect rule both have to be proved on each.


@pytest.mark.asyncio
@respx.mock
async def test_async_invoice_pdf_returns_raw_bytes(async_client: AsyncBillKit) -> None:
    respx.get("https://test.billkit.eu/v1/invoices/inv_1/pdf").mock(
        return_value=httpx.Response(200, content=PDF)
    )
    assert await async_client.invoices.retrieve_pdf("inv_1") == PDF


@pytest.mark.asyncio
@respx.mock
async def test_async_pdf_redirect_does_not_carry_the_api_key(
    async_client: AsyncBillKit,
) -> None:
    respx.get("https://test.billkit.eu/v1/credit_notes/cn_1/pdf").mock(
        return_value=httpx.Response(302, headers={"location": "https://s3.test/obj?sig=abc"})
    )
    signed = respx.get("https://s3.test/obj").mock(return_value=httpx.Response(200, content=PDF))

    assert await async_client.credit_notes.retrieve_pdf("cn_1") == PDF
    assert "authorization" not in {k.lower() for k in signed.calls[0].request.headers}


@pytest.mark.asyncio
@respx.mock
async def test_async_pdf_still_raises_the_typed_error(async_client: AsyncBillKit) -> None:
    respx.get("https://test.billkit.eu/v1/invoices/inv_1/pdf").mock(
        return_value=httpx.Response(
            501, json={"error": {"type": "api_error", "code": "rendering_pending", "message": "x"}}
        )
    )
    with pytest.raises(ServerError) as excinfo:
        await async_client.invoices.retrieve_pdf("inv_1")
    assert excinfo.value.code == "rendering_pending"
