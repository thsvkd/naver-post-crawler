"""공용 HTTP 재시도 헬퍼(:func:`get_with_retry`) 테스트.

재시도 정책(4xx 즉시 중단 / 5xx·429 재시도)과, 실패 보고가 '실제로 몇 번
시도했는지'를 정확히 담는지를 검증한다.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from naver_post_crawler.errors import FetchError
from naver_post_crawler.http import get_with_retry

logger = logging.getLogger(__name__)


def _client(status: int, calls: dict[str, int]) -> httpx.Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] = calls.get("n", 0) + 1
        return httpx.Response(status)

    return httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handler))


def _get(client: httpx.Client, *, max_retries: int = 3) -> httpx.Response:
    return get_with_retry(client, "/x", delay=0, max_retries=max_retries, logger=logger)


def test_client_error_reports_single_attempt() -> None:
    """covers: Test-6 — 재시도하지 않은 4xx를 'max_retries회 시도'로 부풀리지 않는다."""
    calls: dict[str, int] = {}
    client = _client(400, calls)
    try:
        with pytest.raises(FetchError) as excinfo:
            _get(client)
    finally:
        client.close()

    assert calls["n"] == 1
    assert excinfo.value.attempts == 1
    assert "1회 시도" in str(excinfo.value)


def test_server_error_retries_up_to_max() -> None:
    """covers: Test-7 — 5xx는 max_retries만큼 시도하고 그 횟수를 보고한다."""
    calls: dict[str, int] = {}
    client = _client(500, calls)
    try:
        with pytest.raises(FetchError) as excinfo:
            _get(client)
    finally:
        client.close()

    assert calls["n"] == 3
    assert excinfo.value.attempts == 3


def test_too_many_requests_is_retried() -> None:
    """covers: Test-8 — 429는 4xx지만 재시도 대상이다."""
    calls: dict[str, int] = {}
    client = _client(429, calls)
    try:
        with pytest.raises(FetchError):
            _get(client)
    finally:
        client.close()

    assert calls["n"] == 3
