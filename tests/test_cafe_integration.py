"""카페 통합(파이프라인) 테스트.

단위 테스트가 검증하는 개별 부품(cafe_client·parser·writer)이 **함께 조립됐을 때**
올바르게 동작하는지 실제 파일 출력까지 검증한다: 모킹한 카페 클라이언트 →
``Crawler(parse_body=parse_cafe_body)`` → txt 파일. 실제 네이버 네트워크는 쓰지
않지만, 소스 교체(parse_body 주입)·articleId 파일명·헤더·빈 글 스킵·증분 재개 등
소스 간 경계가 실제로 맞물리는지 확인한다.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from naver_post_crawler.cafe_client import NaverCafeClient
from naver_post_crawler.cafe_ref import CafeReference
from naver_post_crawler.crawler import Crawler, Outcome
from naver_post_crawler.failures import FailureStore
from naver_post_crawler.parser import parse_cafe_body
from naver_post_crawler.writer import find_by_log_no

Handler = Callable[[httpx.Request], httpx.Response]

_CLUB_HTML = '<script>var g_sClubId = "42";</script>'
# 실제 본문(스마트에디터 ONE) — 텍스트 + 이미지 플레이스홀더.
_BODY_101 = (
    '<div class="se-main-container">'
    '<div class="se-component se-text"><div class="se-text-paragraph">가나다 본문</div></div>'
    '<div class="se-component se-image">'
    '<img data-lazy-src="https://img/p.png" src="data:x"></div>'
    "</div>"
)
# 콘텐츠 모듈이 없는 빈 본문 — SKIPPED_EMPTY로 판정돼야 한다.
_BODY_102_EMPTY = '<div class="se-main-container"></div>'


def _cafe_client(handler: Handler) -> NaverCafeClient:
    client = NaverCafeClient(CafeReference(club_url="mycafe"), menu_id=5, delay=0)
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _pipeline_handler(fetched: list[int]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/mycafe":
            return httpx.Response(200, text=_CLUB_HTML)
        if "boardlist-api" in path:
            page = request.url.params.get("page")
            if page == "1":
                return httpx.Response(
                    200,
                    json={
                        "result": {
                            "articleList": [
                                {
                                    "item": {
                                        "articleId": 101,
                                        "subject": "첫 카페글",
                                        "writeDate": 1692576000000,
                                    }
                                },
                                {
                                    "item": {
                                        "articleId": 102,
                                        "subject": "빈 글",
                                        "writeDate": 1692576000000,
                                    }
                                },
                            ]
                        }
                    },
                )
            return httpx.Response(200, json={"result": {"articleList": []}})
        if "cafe-articleapi" in path:
            article_id = int(path.rstrip("/").split("/")[-1])
            fetched.append(article_id)
            body = _BODY_101 if article_id == 101 else _BODY_102_EMPTY
            return httpx.Response(200, json={"result": {"article": {"contentHtml": body}}})
        return httpx.Response(404)

    return handler


def _run(client: NaverCafeClient, out_dir: Path) -> tuple[dict[Outcome, int], FailureStore]:
    failures = FailureStore.load(out_dir)
    crawler = Crawler(client, out_dir, failures, parse_body=parse_cafe_body)
    plan = crawler.build_plan()
    counts: dict[Outcome, int] = {}
    for result in crawler.run(plan):
        counts[result.outcome] = counts.get(result.outcome, 0) + 1
    return counts, failures


def test_cafe_pipeline_writes_file_with_articleid_and_content(tmp_path: Path) -> None:
    fetched: list[int] = []
    client = _cafe_client(_pipeline_handler(fetched))
    try:
        counts, _ = _run(client, tmp_path)
    finally:
        client.close()

    # 콘텐츠 글 1건 저장, 빈 글 1건 스킵.
    assert counts.get(Outcome.WRITTEN) == 1
    assert counts.get(Outcome.SKIPPED_EMPTY) == 1

    # 파일은 articleId(101)로 식별되고, 헤더·본문·이미지 플레이스홀더가 담긴다.
    saved = find_by_log_no(tmp_path, 101)
    assert saved is not None
    text = saved.read_text(encoding="utf-8")
    assert "제목: 첫 카페글" in text
    assert "날짜: 2023-08-21" in text
    assert "주소: https://cafe.naver.com/mycafe/101" in text
    assert "가나다 본문" in text
    assert "[이미지: https://img/p.png]" in text
    # 빈 글은 파일로 남지 않는다.
    assert find_by_log_no(tmp_path, 102) is None


def test_cafe_pipeline_incremental_resume_skips_saved(tmp_path: Path) -> None:
    # 1차 실행으로 101을 저장한다.
    first_fetched: list[int] = []
    client = _cafe_client(_pipeline_handler(first_fetched))
    try:
        _run(client, tmp_path)
    finally:
        client.close()
    assert 101 in first_fetched

    # 2차 실행: 이미 저장된 101은 본문을 다시 받지 않고 건너뛴다(증분 재개).
    second_fetched: list[int] = []
    client2 = _cafe_client(_pipeline_handler(second_fetched))
    try:
        counts, _ = _run(client2, tmp_path)
    finally:
        client2.close()

    assert counts.get(Outcome.SKIPPED_EXISTING) == 1
    assert 101 not in second_fetched  # 본문 재요청 없음
