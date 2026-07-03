"""카페 내부 API 클라이언트 테스트.

실제 네트워크 대신 ``httpx.MockTransport``로 내부 API 응답을 흉내 내어, clubId
해석·게시판 필터·목록 페이지네이션·본문 조회·로그인 판별·응답 봉투 처리를 검증한다.
비공식 API라 라이브 응답 구조와 다를 수 있으므로, 여기서 검증하는 것은 클라이언트의
"파싱·흐름 로직"이다.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from naver_blog_crawler.cafe_client import NaverCafeClient, _write_ms
from naver_blog_crawler.cafe_ref import CafeReference
from naver_blog_crawler.errors import CafeNotFound, LoginRequired, ParseError
from naver_blog_crawler.models import PostMeta

Handler = Callable[[httpx.Request], httpx.Response]

_CLUB_HTML = '<script>var g_sClubId = "29434212";</script>'
_SE_ONE_HTML = (
    '<div class="se-main-container">'
    '<div class="se-component se-text"><div class="se-text-paragraph">카페 본문</div></div>'
    "</div>"
)


def _cafe_client(
    handler: Handler, ref: CafeReference | None = None, **kw: object
) -> NaverCafeClient:
    """MockTransport로 응답하는 카페 클라이언트를 만든다(헤더는 보존해 쿠키 검증 가능)."""
    ref = ref or CafeReference(club_url="steamindiegame")
    client = NaverCafeClient(ref, delay=0, **kw)  # type: ignore[arg-type]
    headers = client._client.headers
    client._client.close()
    client._client = httpx.Client(headers=headers, transport=httpx.MockTransport(handler))
    return client


def _articles(items: list[tuple]) -> httpx.Response:
    """boardlist-api 목록 응답을 만든다.

    각 항목은 (articleId, subject) 또는 (articleId, subject, menuId). 실제 응답처럼
    ``{"type": "ARTICLE", "item": {...}}`` 형태로 감싸고, menuId가 주어지면 항목에 넣는다.
    """
    article_list = []
    for it in items:
        aid, subject = it[0], it[1]
        node: dict[str, object] = {
            "articleId": aid,
            "subject": subject,
            "writeDateTimestamp": 1692576000000,
        }
        if len(it) > 2:
            node["menuId"] = it[2]
        article_list.append({"type": "ARTICLE", "item": node})
    return httpx.Response(200, json={"result": {"articleList": article_list}})


def _default_handler(request: httpx.Request) -> httpx.Response:
    """clubId 해석 → 전체글 목록 → 본문의 정상 경로를 흉내 낸다.

    전체글(menuId 0)/특정 게시판 구분 없이 boardlist-api 1페이지에 두 글을 주고,
    각 글에 서로 다른 실제 menuId(1·4)를 담아 '실제 menuId 캡처'를 검증할 수 있게 한다.
    """
    path = request.url.path
    if path == "/steamindiegame":
        return httpx.Response(200, text=_CLUB_HTML)
    if "boardlist-api" in path:
        page = request.url.params.get("page")
        if page == "1":
            return _articles([(101, "글 하나", 1), (102, "글 둘", 4)])
        return _articles([])
    if "cafe-articleapi" in path:
        return httpx.Response(
            200, json={"result": {"article": {"subject": "글 하나", "contentHtml": _SE_ONE_HTML}}}
        )
    return httpx.Response(404, json={"error": {"code": "not_found"}})


def test_resolve_cafe_id_from_g_sclubid() -> None:
    client = _cafe_client(_default_handler)
    try:
        assert client.cafe_id == 29434212
    finally:
        client.close()


def test_resolve_cafe_id_raises_when_missing() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>카페 없음</html>")

    client = _cafe_client(handler)
    try:
        with pytest.raises(CafeNotFound):
            _ = client.cafe_id
    finally:
        client.close()


def test_default_uses_all_articles_and_captures_real_menu() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _default_handler(request)

    client = _cafe_client(handler)
    try:
        metas = list(client.iter_post_meta())
    finally:
        client.close()

    assert [m.log_no for m in metas] == [101, 102]
    assert [m.title for m in metas] == ["글 하나", "글 둘"]
    assert metas[0].date_str == "2023-08-21"
    # menu 미지정 시 전체글(menuId 0) 목록을 조회한다.
    assert any("/menus/0/articles" in path for path in calls)
    # 본문 조회용 매핑은 iteration 값(0)이 아니라 각 글의 '실제' menuId(1·4)여야 한다.
    assert client._menu_of == {101: 1, 102: 4}


def test_explicit_menu_used_without_home_or_discovery() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _default_handler(request)

    client = _cafe_client(handler, ref=CafeReference(cafe_id=29434212), menu_id=5)
    try:
        metas = list(client.iter_post_meta())
    finally:
        client.close()

    assert [m.log_no for m in metas] == [101, 102]
    # 지정 게시판(5)만 조회하고, cafe_id가 이미 있으니 홈(clubId 해석) 요청도 없다.
    assert any("/menus/5/articles" in path for path in calls)
    assert not any(path == "/steamindiegame" for path in calls)


def test_fetch_post_html_returns_content_html() -> None:
    client = _cafe_client(_default_handler)
    try:
        _ = client.cafe_id
        html = client.fetch_post_html(101)
    finally:
        client.close()
    assert "se-main-container" in html
    assert "카페 본문" in html


def test_fetch_post_html_missing_content_raises_parse_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/steamindiegame":
            return httpx.Response(200, text=_CLUB_HTML)
        if "cafe-articleapi" in request.url.path:
            return httpx.Response(200, json={"result": {"article": {"contentHtml": ""}}})
        return httpx.Response(404)

    client = _cafe_client(handler)
    try:
        _ = client.cafe_id
        with pytest.raises(ParseError):
            client.fetch_post_html(101)
    finally:
        client.close()


def test_login_required_detected_from_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/steamindiegame":
            return httpx.Response(200, text=_CLUB_HTML)
        if "cafe-articleapi" in request.url.path:
            return httpx.Response(
                200, json={"error": {"code": "AUTH", "msg": "로그인이 필요합니다."}}
            )
        return httpx.Response(404)

    client = _cafe_client(handler)
    try:
        _ = client.cafe_id
        with pytest.raises(LoginRequired):
            client.fetch_post_html(101)
    finally:
        client.close()


def test_single_article_mode_fetches_once() -> None:
    hits = {"article": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/steamindiegame":
            return httpx.Response(200, text=_CLUB_HTML)
        if "cafe-articleapi" in path:
            hits["article"] += 1
            return httpx.Response(
                200,
                json={
                    "result": {
                        "article": {
                            "subject": "단일 글",
                            "writeDate": 1692576000000,
                            "menu": {"id": 5},
                            "contentHtml": _SE_ONE_HTML,
                        }
                    }
                },
            )
        return httpx.Response(404)

    client = _cafe_client(handler, ref=CafeReference(club_url="steamindiegame", article_id=555))
    try:
        metas = list(client.iter_post_meta())
        assert [m.log_no for m in metas] == [555]
        assert metas[0].title == "단일 글"
        # 본문은 캐시돼 재요청되지 않는다(iter에서 1회 조회 후 fetch는 캐시 사용).
        html = client.fetch_post_html(555)
    finally:
        client.close()
    assert "카페 본문" in html
    assert hits["article"] == 1


def test_cookie_header_is_sent() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["cookie"] = request.headers.get("Cookie", "")
        return httpx.Response(200, text=_CLUB_HTML)

    client = _cafe_client(handler, cookie="NID_AUT=abc; NID_SES=def")
    try:
        _ = client.cafe_id
    finally:
        client.close()
    assert seen["cookie"] == "NID_AUT=abc; NID_SES=def"


def test_write_ms_accepts_numeric_and_iso_strings() -> None:
    # 정수 ms는 그대로, 정수 초는 ms로 보정.
    assert _write_ms({"writeDate": 1692576000000}) == 1692576000000
    assert _write_ms({"writeDate": 1692576000}) == 1692576000000
    # 숫자 문자열도 수용한다(비공식 API가 문자열로 줄 수 있음).
    assert _write_ms({"writeDate": "1692576000000"}) == 1692576000000
    # ISO 문자열은 KST로 간주해 ms로 변환한다. 자정 근처 값이라, UTC로 잘못 보면
    # 날짜가 하루 밀려(2023-08-22) 이 단언이 tz 처리를 강하게 잠근다.
    ms = _write_ms({"writeDate": "2023-08-21 23:30:00"})
    meta = PostMeta(log_no=1, title="t", add_date_ms=ms, is_anniversary=False)
    assert meta.date_str == "2023-08-21"
    # 알 수 없는 형식은 0(폴백).
    assert _write_ms({"writeDate": "어제"}) == 0
    assert _write_ms({}) == 0


def test_duplicate_article_across_pages_emitted_once() -> None:
    # 같은 글이 여러 페이지에 다시 나타나도 한 번만 방출한다. 페이지에 새 글이
    # 하나라도 있으면 계속 진행하고, 빈 페이지에서 종료한다.
    def handler(request: httpx.Request) -> httpx.Response:
        if "boardlist-api" not in request.url.path:
            return httpx.Response(404)
        page = request.url.params.get("page")
        if page == "1":
            return _articles([(1, "글1", 2), (2, "글2", 2)])
        if page == "2":
            return _articles([(2, "글2", 2), (3, "글3", 2)])  # 2는 중복, 3은 신규
        return _articles([])

    client = _cafe_client(handler, ref=CafeReference(cafe_id=29434212))
    try:
        metas = list(client.iter_post_meta())
    finally:
        client.close()

    assert [m.log_no for m in metas] == [1, 2, 3]
    assert client._menu_of == {1: 2, 2: 2, 3: 2}


def test_repeated_page_terminates_pagination() -> None:
    # API가 같은 '비어있지 않은' 페이지를 무한 반복해도, 무한루프 가드
    # (progressed=False)로 종료해야 한다. 안전상한을 둬, 종료하지 않으면
    # RuntimeError로 실패시킨다(빈 페이지 종료 조건만으로는 못 잡는 경로).
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "boardlist-api" in path:
            calls["n"] += 1
            if calls["n"] > 50:
                raise RuntimeError("페이지네이션이 종료되지 않음(무한 루프)")
            # 항상 같은 비어있지 않은 페이지를 돌려준다(빈 페이지 종료 조건에 안 걸림).
            return _articles([(1, "글1"), (2, "글2")])
        return httpx.Response(404)

    client = _cafe_client(handler, ref=CafeReference(cafe_id=29434212))
    try:
        ids = [m.log_no for m in client.iter_post_meta()]
    finally:
        client.close()

    # 첫 페이지만 방출하고, 두 번째(동일) 페이지에서 진행 없음으로 종료한다.
    assert ids == [1, 2]
    assert calls["n"] <= 2


def test_forbidden_status_raises_login_required() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/steamindiegame":
            return httpx.Response(200, text=_CLUB_HTML)
        if "cafe-articleapi" in request.url.path:
            return httpx.Response(403, json={})
        return httpx.Response(404)

    client = _cafe_client(handler)
    try:
        _ = client.cafe_id
        with pytest.raises(LoginRequired):
            client.fetch_post_html(101)
    finally:
        client.close()


def test_post_url_uses_club_url_or_spa() -> None:
    named = _cafe_client(_default_handler, ref=CafeReference(club_url="steamindiegame"))
    try:
        assert named.post_url(101) == "https://cafe.naver.com/steamindiegame/101"
    finally:
        named.close()

    numeric = _cafe_client(_default_handler, ref=CafeReference(cafe_id=29434212), menu_id=5)
    try:
        assert numeric.post_url(101) == "https://cafe.naver.com/f-e/cafes/29434212/articles/101"
    finally:
        numeric.close()
