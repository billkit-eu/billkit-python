"""HTTPX-backed transport with shared retry + error mapping.

Both :class:`billkit.BillKit` (sync) and :class:`billkit.AsyncBillKit`
delegate to a transport in this module. The async transport wraps
``httpx.AsyncClient``; the sync transport wraps ``httpx.Client``.
They share :func:`_build_request_kwargs`, :func:`_raise_for_status`,
and the retry decision in :mod:`billkit._retry` so behaviour is
identical across both surfaces.

The async retry backoff sleeps through ``anyio`` (already installed with
``httpx``), not ``asyncio``, so an ``AsyncBillKit`` used under trio waits
between attempts instead of failing on the first retry.

Each transport exposes one retry loop, ``_send``, returning the raw 2xx
response. ``request`` decodes it as JSON and ``request_bytes`` hands back
the bytes, so the document routes (invoice and credit-note PDFs) inherit
the same timeout, retry budget and typed errors as every resource call
rather than carrying a second copy of them.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

import anyio
import httpx

from billkit._errors import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    BillKitError,
    error_from_response,
)
from billkit._logging import logger, quiet_leaky_request_logs, sdk_logging_configured
from billkit._models import _BillKitModel, construct
from billkit._options import DEFAULT_OPTIONS, NotGiven, RequestOptions
from billkit._retry import DEFAULT_RETRY_POLICY, RetryPolicy, should_retry
from billkit._version import __version__

_M = TypeVar("_M", bound=_BillKitModel)

DEFAULT_BASE_URL = "https://api.billkit.eu"
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _user_agent() -> str:
    return f"billkit-python/{__version__} httpx/{httpx.__version__}"


#: Headers the SDK owns. ``extra_headers`` cannot set them: an
#: ``authorization`` in a different case went out *beside* the SDK's own
#: (two credentials on one request), and an ``Idempotency-Key`` there
#: bypassed the blank-key guard in :func:`auto_idempotency_key`, so a
#: retried POST could carry no usable key at all.
_RESERVED_HEADERS = {
    "authorization": "pass a different api_key to the client instead",
    "idempotency-key": "pass idempotency_key= to the method instead",
    "content-type": "the SDK sets it from the request body",
    "user-agent": "the SDK identifies itself; add your own X- header instead",
}


def _check_extra_headers(extra_headers: Mapping[str, str]) -> None:
    for name in extra_headers:
        hint = _RESERVED_HEADERS.get(name.lower()) if isinstance(name, str) else None
        if hint is not None:
            raise ValueError(f"extra_headers cannot set {name!r}: {hint}.")


def _merge_extras(
    method: str,
    params: dict[str, Any] | None,
    json_body: dict[str, Any] | None,
    options: RequestOptions,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Fold ``extra_query`` / ``extra_body`` over what the method built.

    Copies, never mutates: the method's dicts are rebuilt per call today,
    but a retry re-sending a dict someone else changed underneath it is
    not a hazard worth leaving in.
    """
    if options.extra_query:
        params = {**(params or {}), **options.extra_query}
    if options.extra_body:
        if method.upper() in ("GET", "HEAD", "DELETE"):
            raise ValueError(f"extra_body cannot be sent on a {method.upper()} request.")
        if not isinstance(options.extra_body, Mapping):
            raise TypeError("extra_body must be a mapping of top-level body fields.")
        json_body = {**(json_body or {}), **options.extra_body}
    return params, json_body


def _build_request_kwargs(
    *,
    api_key: str,
    method: str,
    path: str,
    params: dict[str, Any] | None,
    json_body: dict[str, Any] | None,
    idempotency_key: str | None,
    extra_headers: Mapping[str, str] | None,
    timeout: httpx.Timeout | NotGiven | None,
    follow_redirects: bool = False,
) -> dict[str, Any]:
    if extra_headers:
        _check_extra_headers(extra_headers)
    # ``httpx.Headers`` is case-insensitive, so ``x-trace`` and ``X-Trace``
    # are one header, replaced rather than sent twice.
    headers = httpx.Headers(
        {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": _user_agent(),
            "Accept": "application/json",
        }
    )
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
    if not isinstance(timeout, NotGiven):
        kwargs["timeout"] = timeout
    return kwargs


def _decode_json(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
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
        headers=response.headers,
        raw_text=response.text if response.content else None,
    )


def _decode_success(response: httpx.Response, cast_to: type[_M]) -> _M:
    """Build the response model from a 2xx body.

    An empty body builds an object with no keys rather than failing, so a
    future 204 on some route reads as "nothing to report".

    A body that is there but is not JSON is refused with an
    :class:`APIError` carrying the status, request id, headers and the raw
    text. It is usually a proxy or captive portal answering in BillKit's
    place, and handing back an empty object let the caller carry on as if
    the call had worked. A body that is JSON but not an object cannot be
    any BillKit response either; the request did succeed, so that error
    says so.
    """
    if not response.content:
        return construct(cast_to, {})
    try:
        decoded = response.json()
    except ValueError:
        raise APIError(
            f"BillKit API answered HTTP {response.status_code} with a body that is not "
            f"JSON (request_id={_request_id(response) or '-'}). Check that base_url "
            "points at the API.",
            status_code=response.status_code,
            request_id=_request_id(response),
            raw_body=response.text,
            headers=response.headers,
        ) from None
    if not isinstance(decoded, dict):
        raise BillKitError(
            f"BillKit API returned HTTP {response.status_code} with a JSON "
            f"{type(decoded).__name__} where an object was expected "
            f"(request_id={_request_id(response) or '-'}). The request itself succeeded."
        )
    return construct(cast_to, decoded)


def _connection_error(exc: httpx.HTTPError) -> APIConnectionError:
    if isinstance(exc, httpx.TimeoutException):
        return APITimeoutError(f"Request timed out: {exc}")
    return APIConnectionError(str(exc) or type(exc).__name__)


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


def _resolve_timeout(
    value: float | httpx.Timeout | NotGiven | None,
) -> httpx.Timeout | NotGiven | None:
    if isinstance(value, NotGiven) or value is None or isinstance(value, httpx.Timeout):
        return value
    return httpx.Timeout(value)


def auto_idempotency_key(method: str, supplied: str | None) -> str | None:
    """Mutating methods (POST/PUT/PATCH/DELETE) get a generated
    ``Idempotency-Key`` so a retry safely converges. GETs don't.

    Callers can supply their own key to coalesce retries across
    process restarts. An empty or whitespace-only key is treated as not
    supplied and replaced with a generated one."""
    if method == "GET":
        return None
    # A blank or whitespace-only key counts as "not supplied". Passing it
    # through sent ``Idempotency-Key:`` with no value while the retry loop
    # re-sent the POST, so ``idempotency_key=os.environ.get("X", "")``
    # quietly turned off the one thing that makes a retried charge safe.
    # Generated here, once, before the loop, so every attempt carries it.
    if supplied is not None and supplied.strip():
        return supplied
    return f"sdk-{uuid.uuid4()}"


class _BaseTransport:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: httpx.Timeout | NotGiven | None,
        retry_policy: RetryPolicy,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        #: The client-level timeout to send with every request, or
        #: ``NOT_GIVEN`` to leave an injected httpx client's own default alone.
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

    def _prepare(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        options: RequestOptions,
        follow_redirects: bool,
    ) -> tuple[str, dict[str, Any], RetryPolicy]:
        """Everything decided once per call, before the retry loop.

        The idempotency key in particular is fixed here and never again,
        so every attempt of one logical call carries the same key.
        """
        self._quiet_httpx_once()
        url = self._path(path)
        timeout = _resolve_timeout(options.timeout)
        if isinstance(timeout, NotGiven):
            timeout = self._timeout
        params, json_body = _merge_extras(method, params, json_body, options)
        kwargs = _build_request_kwargs(
            api_key=self._api_key,
            method=method,
            path=url,
            params=params,
            json_body=json_body,
            idempotency_key=auto_idempotency_key(method, options.idempotency_key),
            extra_headers=options.extra_headers,
            timeout=timeout,
            follow_redirects=follow_redirects,
        )
        policy = self._retry_policy
        if not isinstance(options.max_retries, NotGiven):
            if options.max_retries < 0:
                raise ValueError(f"max_retries must be 0 or more, got {options.max_retries}.")
            policy = replace(policy, max_attempts=options.max_retries + 1)
        return url, kwargs, policy

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
        timeout: httpx.Timeout | NotGiven | None = DEFAULT_TIMEOUT,
        retry_policy: RetryPolicy | None = None,
        httpx_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            retry_policy=retry_policy or DEFAULT_RETRY_POLICY,
        )
        self._owned = httpx_client is None
        self._client = httpx_client or httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT if isinstance(timeout, NotGiven) else timeout
        )
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
        cast_to: type[_M],
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        options: RequestOptions = DEFAULT_OPTIONS,
    ) -> _M:
        """Perform one API call and build its response model."""
        response = await self._send(
            method, path, params=params, json_body=json_body, options=options
        )
        return _decode_success(response, cast_to)

    async def request_bytes(
        self, method: str, path: str, *, options: RequestOptions = DEFAULT_OPTIONS
    ) -> bytes:
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
        response = await self._send(method, path, options=options, follow_redirects=True)
        return response.content

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        options: RequestOptions = DEFAULT_OPTIONS,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        """The retry loop. Returns the first 2xx response, or raises."""
        url, kwargs, policy = self._prepare(
            method,
            path,
            params=params,
            json_body=json_body,
            options=options,
            follow_redirects=follow_redirects,
        )
        last_exc: BillKitError | None = None
        for attempt in range(1, policy.max_attempts + 1):
            _log_request(method, url, attempt, policy.max_attempts)
            started = time.perf_counter()
            try:
                response = await self._client.request(**kwargs)
            except httpx.HTTPError as exc:
                last_exc = _connection_error(exc)
                if not should_retry(None, attempt=attempt, policy=policy):
                    raise last_exc from exc
                delay = _retry_delay(response=None, attempt=attempt, policy=policy)
                _log_retry(method, url, type(exc).__name__, attempt, delay)
                await anyio.sleep(delay)
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
            except APIError as exc:
                # ``exc.code`` is what separates a transient
                # ``409 idempotency_in_progress`` from every other
                # (permanent) 409; see ``_retry.IN_PROGRESS_CODE``. The key
                # on the wire is unchanged across attempts, so the retry
                # replays rather than re-charges.
                if not should_retry(
                    response.status_code,
                    attempt=attempt,
                    policy=policy,
                    retry_after_seconds=_retry_after(response),
                    error_code=exc.code,
                ):
                    raise
                last_exc = exc
                delay = _retry_delay(response=response, attempt=attempt, policy=policy)
                _log_retry(method, url, f"HTTP {response.status_code}", attempt, delay)
                await anyio.sleep(delay)
        # Not `assert last_exc is not None`: asserts are stripped under
        # `python -O`, which would turn this into `raise None` ->
        # `TypeError: exceptions must derive from BaseException`. The
        # loop always runs at least once (`RetryPolicy` refuses
        # `max_attempts < 1`), so this fallback only keeps a real exception
        # here should that ever change. Node and PHP both raise an explicit
        # error here; match them.
        raise last_exc or APIConnectionError(
            f"Retry budget exhausted with no recorded error (max_attempts={policy.max_attempts})."
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
        timeout: httpx.Timeout | NotGiven | None = DEFAULT_TIMEOUT,
        retry_policy: RetryPolicy | None = None,
        httpx_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            retry_policy=retry_policy or DEFAULT_RETRY_POLICY,
        )
        self._owned = httpx_client is None
        self._client = httpx_client or httpx.Client(
            timeout=DEFAULT_TIMEOUT if isinstance(timeout, NotGiven) else timeout
        )
        if self._owned:
            # See AsyncTransport.__init__.
            self._httpx_quieted = bool(quiet_leaky_request_logs())

    def request(
        self,
        method: str,
        path: str,
        *,
        cast_to: type[_M],
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        options: RequestOptions = DEFAULT_OPTIONS,
    ) -> _M:
        """Perform one API call and build its response model."""
        response = self._send(method, path, params=params, json_body=json_body, options=options)
        return _decode_success(response, cast_to)

    def request_bytes(
        self, method: str, path: str, *, options: RequestOptions = DEFAULT_OPTIONS
    ) -> bytes:
        """Fetch a document route's raw bytes. See
        :meth:`AsyncTransport.request_bytes`."""
        response = self._send(method, path, options=options, follow_redirects=True)
        return response.content

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        options: RequestOptions = DEFAULT_OPTIONS,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        """The retry loop. Returns the first 2xx response, or raises."""
        url, kwargs, policy = self._prepare(
            method,
            path,
            params=params,
            json_body=json_body,
            options=options,
            follow_redirects=follow_redirects,
        )
        last_exc: BillKitError | None = None
        for attempt in range(1, policy.max_attempts + 1):
            _log_request(method, url, attempt, policy.max_attempts)
            started = time.perf_counter()
            try:
                response = self._client.request(**kwargs)
            except httpx.HTTPError as exc:
                last_exc = _connection_error(exc)
                if not should_retry(None, attempt=attempt, policy=policy):
                    raise last_exc from exc
                delay = _retry_delay(response=None, attempt=attempt, policy=policy)
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
            except APIError as exc:
                # ``exc.code`` is what separates a transient
                # ``409 idempotency_in_progress`` from every other
                # (permanent) 409; see ``_retry.IN_PROGRESS_CODE``. The key
                # on the wire is unchanged across attempts, so the retry
                # replays rather than re-charges.
                if not should_retry(
                    response.status_code,
                    attempt=attempt,
                    policy=policy,
                    retry_after_seconds=_retry_after(response),
                    error_code=exc.code,
                ):
                    raise
                last_exc = exc
                delay = _retry_delay(response=response, attempt=attempt, policy=policy)
                _log_retry(method, url, f"HTTP {response.status_code}", attempt, delay)
                time.sleep(delay)
        # See AsyncTransport._send for why this is not an `assert`.
        raise last_exc or APIConnectionError(
            f"Retry budget exhausted with no recorded error (max_attempts={policy.max_attempts})."
        )

    def close(self) -> None:
        if self._owned:
            self._client.close()
