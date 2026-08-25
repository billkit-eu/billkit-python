"""HTTPX-backed transport with shared retry + error mapping.

Both :class:`billkit.BillKit` (sync) and :class:`billkit.AsyncBillKit`
delegate to a transport in this module. The async transport wraps
``httpx.AsyncClient``; the sync transport wraps ``httpx.Client``.
They share :func:`_build_request_kwargs`, :func:`_handle_response`,
and the retry decision in :mod:`billkit._retry` so behaviour is
identical across both surfaces.
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
from billkit._logging import logger
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


def _handle_response(response: httpx.Response) -> dict[str, Any]:
    if 200 <= response.status_code < 300:
        decoded = _decode_json(response)
        # Normalise an empty/204 body to ``{}`` so callers always get a
        # dict, with no ``| None`` to narrow at every call site.
        return decoded if decoded is not None else {}
    raise error_from_response(
        response.status_code,
        _decode_json(response),
        request_id=_request_id(response),
        retry_after=_retry_after(response),
    )


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

    def _path(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self._base_url + path


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
        url = self._path(path)
        kwargs = _build_request_kwargs(
            api_key=self._api_key,
            method=method,
            path=url,
            params=params,
            json_body=json_body,
            idempotency_key=auto_idempotency_key(method, idempotency_key),
            extra_headers=extra_headers,
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
                return _handle_response(response)
            except BillKitError as exc:
                retry_after = _retry_after(response)
                if not should_retry(
                    response.status_code,
                    attempt=attempt,
                    policy=self._retry_policy,
                    retry_after_seconds=retry_after,
                ):
                    raise
                last_exc = exc
                delay = _retry_delay(
                    response=response, attempt=attempt, policy=self._retry_policy
                )
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
        url = self._path(path)
        kwargs = _build_request_kwargs(
            api_key=self._api_key,
            method=method,
            path=url,
            params=params,
            json_body=json_body,
            idempotency_key=auto_idempotency_key(method, idempotency_key),
            extra_headers=extra_headers,
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
                return _handle_response(response)
            except BillKitError as exc:
                retry_after = _retry_after(response)
                if not should_retry(
                    response.status_code,
                    attempt=attempt,
                    policy=self._retry_policy,
                    retry_after_seconds=retry_after,
                ):
                    raise
                last_exc = exc
                delay = _retry_delay(
                    response=response, attempt=attempt, policy=self._retry_policy
                )
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
