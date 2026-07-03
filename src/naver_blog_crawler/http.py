"""재시도·백오프를 적용한 HTTP GET 헬퍼.

블로그·카페 클라이언트가 공유하는 요청 로직이다. 요청 사이 대기(예의)를 두고,
일시적 장애·5xx·429는 지수 백오프로 재시도하며, 재시도해도 의미 없는 4xx(429
제외)는 즉시 중단한다. 도메인별 치명적 4xx(예: 존재하지 않는 블로그/카페)는
``fatal`` 콜백으로 분기해 원인이 분명한 예외로 바꾼다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import httpx

from .errors import CrawlerError, FetchError

# HTTPStatusError를 받아, 재시도 없이 즉시 중단할 도메인 예외를 돌려준다(아니면 None).
Fatal = Callable[[httpx.HTTPStatusError], CrawlerError | None]


def get_with_retry(
    client: httpx.Client,
    path: str,
    params: dict[str, object] | None = None,
    *,
    delay: float,
    max_retries: int,
    logger: logging.Logger,
    fatal: Fatal | None = None,
) -> httpx.Response:
    """딜레이·지수 백오프 재시도를 적용해 GET 요청을 수행한다.

    Args:
        client: 요청을 보낼 httpx 클라이언트.
        path: 요청 경로 또는 절대 URL.
        params: 쿼리 파라미터.
        delay: 요청 전 대기 시간(초).
        max_retries: 최대 시도 횟수.
        logger: 재시도·최종 실패를 남길 로거.
        fatal: 특정 4xx를 재시도 없이 즉시 중단할 도메인 예외로 바꾸는 콜백.

    Raises:
        CrawlerError: ``fatal``이 돌려준 도메인 예외(예: BlogNotFound).
        FetchError: 재시도 이후에도 요청이 실패한 경우.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        if delay:
            time.sleep(delay)
        try:
            resp = client.get(path, params=params)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            status = exc.response.status_code
            # 도메인상 재시도가 의미 없는 4xx(없는 블로그/카페 등)는 즉시 중단한다.
            if fatal is not None:
                domain_exc = fatal(exc)
                if domain_exc is not None:
                    raise domain_exc from exc
            if 400 <= status < 500 and status != 429:
                break
        except httpx.HTTPError as exc:
            last_exc = exc
        # 마지막 시도가 아니면 점증 대기 후 재시도한다.
        if attempt < max_retries - 1:
            logger.warning(
                "요청 실패, 재시도 %d/%d: %s (%r)",
                attempt + 1,
                max_retries,
                path,
                last_exc,
            )
            time.sleep(delay * (2**attempt))
    url = str(client.build_request("GET", path, params=params).url)
    # 어떤 요청이 최종 실패했는지만 남긴다. 트레이스백은 상위(crawler)에서
    # exc_info로 한 번만 기록하고, 원인은 __cause__로 연결해 넘긴다.
    logger.error("요청 최종 실패(%d회): %s", max_retries, url)
    raise FetchError(url, attempts=max_retries, cause=last_exc) from last_exc
