"""HTTPX-backed transport with shared retry + error mapping.

Both :class:`billkit.BillKit` (sync) and :class:`billkit.AsyncBillKit`
delegate to a transport in this module. The async transport wraps
``httpx.AsyncClient``; the sync transport wraps ``httpx.Client``.
They share :func:`_build_request_kwargs`, :func:`_raise_for_status`,
and the retry decision in :mod:`billkit._retry` so behaviour is
identical across both surfaces.

Each transport exposes one retry loop, ``_send``, returning the raw 2xx
response. ``request`` decodes it as JSON and ``request_bytes`` hands back
the bytes, so the document routes (invoice and credit-note PDFs) inherit
the same timeout, retry budget and typed errors as every resource call
rather than carrying a second copy of them.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from billkit._errors import APIConnectionError, BillKitError, error_from_response
from billkit._logging import logger, quiet_leaky_request_logs, sdk_logging_configured
from billkit._retry import DEFAULT_RETRY_POLICY, RetryPolicy, should_retry
from billkit._version import __version__

DEFAULT_BASE_URL = "https://api.billkit.eu"
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _user_agent() -> str:
    return f"billkit-python/{__version__} httpx/{httpx.__version__}"


def _build_request_kwargs(
    *,
    api_key: str,
    method: str,
    path: str,
    params: dict[str, Any] | None,
    json_body: dict[str, Any] | None,
    idempotency_key: str | None,
    extra_headers: dict[str, str] | None,
    follow_redirects: bool = False,
) -> dict[str, Any]:
    headers: dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": _user_agent(),
        "Accept": "application/json",
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    if extra_headers:
        headers.update(extra_headers)
    kwargs: dict[str, Any] = {
        "method": method,
        "url": path,
        "headers": headers,
    }
    if params is not None:
        kwargs["params"] = {k: v for k, v in params.items() if v is not None}
    if json_body is not None:
        kwargs["json"] = json_body
    if follow_redirects:
        kwargs["follow_redirects"] = True
    return kwargs


def _decode_json(response: httpx.Response) -> dict[str, Any] | None:
    if not response.content:
        return None
    try:
        return response.json()  # type: ignore[no-any-return]
    except ValueError:
        return None


def _request_id(response: httpx.Response) -> str | None:
    raw = response.headers.get("x-request-id") or response.headers.get("request-id")
    return raw if raw else None


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, retry_at.timestamp() - datetime.now(UTC).timestamp())
    return value if value >= 0 else None


def _retry_delay(
    *,
    response: httpx.Response | None,
    attempt: int,
    policy: RetryPolicy,
) -> float:
    if response is not None and response.status_code == 429:
        retry_after = _retry_after(response)
        if retry_after is not None:
            return retry_after
    return policy.backoff_for(attempt + 1)


def _raise_for_status(response: httpx.Response) -> None:
    """Turn a non-2xx into the right typed error, or return quietly.

    Split from decoding because the two response shapes the SDK reads —
    JSON resources and PDF bytes — fail identically. An error is an error
    envelope whichever endpoint produced it, so the document routes throw
    the same typed errors as everything else rather than a shapeless one.
    """
    if 200 <= response.status_code < 300:
        return
    raise error_from_response(
        response.status_code,
        _decode_json(response),
        request_id=_request_id(response),
        retry_after=_retry_after(response),
    )


def _decode_success(response: httpx.Response) -> dict[str, Any]:
    # Normalise an empty/204 body to ``{}`` so callers always get a dict,
    # with no ``| None`` to narrow at every call site.
    decoded = _decode_json(response)
    return decoded if decoded is not None else {}


# --- logging helpers -------------------------------------------------
#
# Shared by both transports so the sync and async surfaces emit byte-for-
# byte identical lines. `%`-style args (never f-strings) keep formatting
# lazy: with the logger disabled (the default) nothing is interpolated.
#
# `url` here is base_url + path with NO query string: `_build_request_kwargs`
# keeps params separate and we never join them. See `billkit._logging` for
# the full list of what is withheld and why.


def _log_request(method: str, url: str, attempt: int, max_attempts: int) -> None:
    logger.debug("BillKit request %s %s (attempt %d/%d)", method, url, attempt, max_attempts)


def _log_response(
    method: str, url: str, status: int, elapsed_ms: float, request_id: str | None
) -> None:
    logger.debug(
        "BillKit response %s %s -> %d in %.0fms (request_id=%s)",
        method,
        url,
        status,
        elapsed_ms,
        request_id or "-",
    )


def _log_retry(method: str, url: str, reason: str, attempt: int, delay_seconds: float) -> None:
    logger.warning(
        "BillKit retrying %s %s after %s (attempt %d) in %.0fms",
        method,
        url,
        reason,
        attempt,
        delay_seconds * 1000,
    )


def auto_idempotency_key(method: str, supplied: str | None) -> str | None:
    """Mutating methods (POST/PUT/PATCH/DELETE) get a generated
    ``Idempotency-Key`` so a retry safely converges. GETs don't.

    Callers can supply their own key to coalesce retries across
    process restarts."""
    if method == "GET":
        return None
    if supplied is not None:
        return supplied
    return f"sdk-{uuid.uuid4()}"


class _BaseTransport:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: httpx.Timeout,
        retry_policy: RetryPolicy,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retry_policy = retry_policy
        # Set properly by each subclass once it knows whether it created
        # the httpx client; declared here so `_quiet_httpx_once` can read
        # both flags off the base.
        self._owned = False
        self._httpx_quieted = False

    def _path(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self._base_url + path

    def _quiet_httpx_once(self) -> None:
        """Re-check httpx's request-line logging on the way into a send.

        The constructor already calls :func:`quiet_leaky_request_logs`,
        which covers the usual startup order. It does not cover the
        opposite one: an app that builds the client first and configures
        the ``billkit`` logger afterwards had nothing quieted, so the
        first request logged a full URL with its query string, the exact
        leak that function exists to prevent.

        Cheap enough to sit in the hot path: one attribute test until the
        app opts in, then one more call, then it latches.
        """
        if self._httpx_quieted or not self._owned:
            return
        if not sdk_logging_configured():
            return
        quiet_leaky_request_logs()
        self._httpx_quieted = True


class AsyncTransport(_BaseTransport):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: httpx.Timeout | None = None,
        retry_policy: RetryPolicy | None = None,
        httpx_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout or DEFAULT_TIMEOUT,
            retry_policy=retry_policy or DEFAULT_RETRY_POLICY,
        )
        self._owned = httpx_client is None
        self._client = httpx_client or httpx.AsyncClient(timeout=self._timeout)
        if self._owned:
            # Only for a client we created. A caller who injected their own
            # ``httpx.AsyncClient`` owns its logging as much as its pooling.
            # ``_quiet_httpx_once`` on the send path covers an app that
            # configures its logging after building the client.
            self._httpx_quieted = bool(quiet_leaky_request_logs())

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Perform one API call and return its decoded JSON body."""
        return _decode_success(
            await self._send(
                method,
                path,
                params=params,
                json_body=json_body,
                idempotency_key=idempotency_key,
                extra_headers=extra_headers,
            )
        )

    async def request_bytes(self, method: str, path: str) -> bytes:
        """Fetch a document route's raw bytes (the invoice + credit-note PDFs).

        Same retry budget, timeout and typed errors as :meth:`request`;
        only the success-path decoding differs.

        ``follow_redirects`` is on for this call alone. S3-backed
        deployments answer ``302`` to a presigned URL while blob-backed
        ones stream the bytes inline, and following it is what makes the
        two storage adapters look identical from here. ``httpx`` drops the
        ``Authorization`` header on a cross-origin redirect, which is
        exactly right: the presigned URL carries its own credential and
        must not be handed BillKit's API key.
        """
        return (await self._send(method, path, follow_redirects=True)).content

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        """The retry loop. Returns the first 2xx response, or raises."""
        self._quiet_httpx_once()
        url = self._path(path)
        kwargs = _build_request_kwargs(
            api_key=self._api_key,
            method=method,
            path=url,
            params=params,
            json_body=json_body,
            idempotency_key=auto_idempotency_key(method, idempotency_key),
            extra_headers=extra_headers,
            follow_redirects=follow_redirects,
        )
        last_exc: BillKitError | None = None
        for attempt in range(1, self._retry_policy.max_attempts + 1):
            _log_request(method, url, attempt, self._retry_policy.max_attempts)
            started = time.perf_counter()
            try:
                response = await self._client.request(**kwargs)
            except httpx.HTTPError as exc:
                last_exc = APIConnectionError(str(exc))
                if not should_retry(None, attempt=attempt, policy=self._retry_policy):
                    raise last_exc from exc
                delay = _retry_delay(response=None, attempt=attempt, policy=self._retry_policy)
                _log_retry(method, url, type(exc).__name__, attempt, delay)
                await asyncio.sleep(delay)
                continue
            _log_response(
                method,
                url,
                response.status_code,
                (time.perf_counter() - started) * 1000,
                _request_id(response),
            )
            try:
                _raise_for_status(response)
                return response
            except BillKitError as exc:
                retry_after = _retry_after(response)
                # ``exc.code`` is what separates a transient
                # ``409 idempotency_in_progress`` from every other
                # (permanent) 409; see ``_retry.IN_PROGRESS_CODE``. The key
                # on the wire is unchanged across attempts, so the retry
                # replays rather than re-charges.
                if not should_retry(
                    response.status_code,
                    attempt=attempt,
                    policy=self._retry_policy,
                    retry_after_seconds=retry_after,
                    error_code=exc.code,
                ):
                    raise
                last_exc = exc
                delay = _retry_delay(response=response, attempt=attempt, policy=self._retry_policy)
                _log_retry(method, url, f"HTTP {response.status_code}", attempt, delay)
                await asyncio.sleep(delay)
        # Not `assert last_exc is not None`: asserts are stripped under
        # `python -O`, which would turn this into `raise None` ->
        # `TypeError: exceptions must derive from BaseException`. The
        # branch is also genuinely reachable with a `max_attempts <= 0`
        # policy, where the loop body never runs. Node and PHP both raise
        # an explicit error here; match them.
        raise last_exc or APIConnectionError(
            "Retry budget exhausted with no recorded error "
            f"(max_attempts={self._retry_policy.max_attempts})."
        )

    async def close(self) -> None:
        if self._owned:
            await self._client.aclose()


class SyncTransport(_BaseTransport):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: httpx.Timeout | None = None,
        retry_policy: RetryPolicy | None = None,
        httpx_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout or DEFAULT_TIMEOUT,
            retry_policy=retry_policy or DEFAULT_RETRY_POLICY,
        )
        self._owned = httpx_client is None
        self._client = httpx_client or httpx.Client(timeout=self._timeout)
        if self._owned:
            # See AsyncTransport.__init__.
            self._httpx_quieted = bool(quiet_leaky_request_logs())

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Perform one API call and return its decoded JSON body."""
        return _decode_success(
            self._send(
                method,
                path,
                params=params,
                json_body=json_body,
                idempotency_key=idempotency_key,
                extra_headers=extra_headers,
            )
        )

    def request_bytes(self, method: str, path: str) -> bytes:
        """Fetch a document route's raw bytes. See
        :meth:`AsyncTransport.request_bytes`."""
        return self._send(method, path, follow_redirects=True).content

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        """The retry loop. Returns the first 2xx response, or raises."""
        self._quiet_httpx_once()
        url = self._path(path)
        kwargs = _build_request_kwargs(
            api_key=self._api_key,
            method=method,
            path=url,
            params=params,
            json_body=json_body,
            idempotency_key=auto_idempotency_key(method, idempotency_key),
            extra_headers=extra_headers,
            follow_redirects=follow_redirects,
        )
        last_exc: BillKitError | None = None
        for attempt in range(1, self._retry_policy.max_attempts + 1):
            _log_request(method, url, attempt, self._retry_policy.max_attempts)
            started = time.perf_counter()
            try:
                response = self._client.request(**kwargs)
            except httpx.HTTPError as exc:
                last_exc = APIConnectionError(str(exc))
                if not should_retry(None, attempt=attempt, policy=self._retry_policy):
                    raise last_exc from exc
                delay = _retry_delay(response=None, attempt=attempt, policy=self._retry_policy)
                _log_retry(method, url, type(exc).__name__, attempt, delay)
                time.sleep(delay)
                continue
            _log_response(
                method,
                url,
                response.status_code,
                (time.perf_counter() - started) * 1000,
                _request_id(response),
            )
            try:
                _raise_for_status(response)
                return response
            except BillKitError as exc:
                retry_after = _retry_after(response)
                # ``exc.code`` is what separates a transient
                # ``409 idempotency_in_progress`` from every other
                # (permanent) 409; see ``_retry.IN_PROGRESS_CODE``. The key
                # on the wire is unchanged across attempts, so the retry
                # replays rather than re-charges.
                if not should_retry(
                    response.status_code,
                    attempt=attempt,
                    policy=self._retry_policy,
                    retry_after_seconds=retry_after,
                    error_code=exc.code,
                ):
                    raise
                last_exc = exc
                delay = _retry_delay(response=response, attempt=attempt, policy=self._retry_policy)
                _log_retry(method, url, f"HTTP {response.status_code}", attempt, delay)
                time.sleep(delay)
        # See AsyncTransport.request for why this is not an `assert`.
        raise last_exc or APIConnectionError(
            "Retry budget exhausted with no recorded error "
            f"(max_attempts={self._retry_policy.max_attempts})."
        )

    def close(self) -> None:
        if self._owned:
            self._client.close()
