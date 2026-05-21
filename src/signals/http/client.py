"""Shared HTTP client used by all source modules in live mode.

Single httpx.Client instance per host (lazy-created). Each host gets its own
token bucket, retry policy, and default headers. The client owns nothing about
which source is calling it — source modules pass `host_key` to route through
the right bucket.

Fixture mode bypasses this entirely; source modules dispatch on
settings.USE_LIVE_APIS before reaching the client.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass

import httpx

from signals.http.rate_limit import TokenBucket

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_DEFAULT_TIMEOUT = 30.0
_MAX_RETRIES = 5


@dataclass(frozen=True)
class HostConfig:
    rate_per_sec: float
    user_agent: str | None = None
    extra_headers: dict[str, str] | None = None


class HttpClient:
    """One-per-process HTTP client with per-host rate limiting + retry."""

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=_DEFAULT_TIMEOUT, follow_redirects=True)
        self._buckets: dict[str, TokenBucket] = {}
        self._configs: dict[str, HostConfig] = {}
        self._lock = threading.Lock()

    def register_host(self, host_key: str, config: HostConfig) -> None:
        with self._lock:
            self._configs[host_key] = config
            self._buckets[host_key] = TokenBucket(rate_per_sec=config.rate_per_sec)

    def get(self, host_key: str, url: str, *, params: dict | None = None,
            headers: dict | None = None) -> httpx.Response:
        return self._request(host_key, "GET", url, params=params, headers=headers)

    def post(self, host_key: str, url: str, *, json: dict | None = None,
             headers: dict | None = None) -> httpx.Response:
        return self._request(host_key, "POST", url, json=json, headers=headers)

    def _request(self, host_key: str, method: str, url: str, **kwargs) -> httpx.Response:
        config = self._configs.get(host_key)
        if config is None:
            raise RuntimeError(f"Host '{host_key}' not registered. Call register_host() first.")

        bucket = self._buckets[host_key]
        merged_headers = dict(config.extra_headers or {})
        if config.user_agent:
            merged_headers["User-Agent"] = config.user_agent
        caller_headers = kwargs.pop("headers", None)
        if caller_headers:
            merged_headers.update(caller_headers)

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            bucket.take(1)
            try:
                resp = self._client.request(method, url, headers=merged_headers, **kwargs)
            except httpx.RequestError as exc:
                last_exc = exc
                wait = (2 ** attempt) + random.uniform(0, 1)
                logger.warning("HTTP transport error on %s %s (attempt %d): %s; retry in %.1fs",
                               method, url, attempt + 1, exc, wait)
                time.sleep(wait)
                continue

            if resp.status_code < 400:
                return resp

            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES - 1:
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                wait = retry_after if retry_after is not None else (2 ** attempt) + random.uniform(0, 1)
                logger.warning("HTTP %d on %s %s (attempt %d); retry in %.1fs",
                               resp.status_code, method, url, attempt + 1, wait)
                time.sleep(wait)
                continue

            # Non-retryable — surface to caller
            resp.raise_for_status()
            return resp  # unreachable but mypy-friendly

        if last_exc:
            raise last_exc
        raise RuntimeError(f"Exhausted {_MAX_RETRIES} retries for {method} {url}")

    def close(self) -> None:
        self._client.close()


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


_singleton: HttpClient | None = None
_singleton_lock = threading.Lock()


def get_http_client() -> HttpClient:
    """Process-wide shared client. Lazy-init to avoid creating one in fixture mode."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = HttpClient()
    return _singleton
