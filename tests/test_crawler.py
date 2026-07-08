"""크롤러 보조 로직 및 실패 기록·재시도 통합 테스트."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

from naver_post_crawler.crawler import Crawler, Outcome, _realign
from naver_post_crawler.errors import FetchError
from naver_post_crawler.failures import FailureStore
from naver_post_crawler.models import KST, PostMeta

_VALID_HTML = (
    '<div class="se-main-container">'
    '<div class="se-component se-text"><div class="se-text-paragraph">본문</div></div>'
    "</div>"
)
_BAD_HTML = "<html><body>컨테이너 없음</body></html>"


class _FakeClient:
    """logNo별로 정해진 HTML 시퀀스를 돌려주는 가짜 클라이언트."""

    def __init__(
        self, responses: dict[int, list[str]], metas: list[PostMeta] | None = None
    ) -> None:
        self._responses = responses
        self._metas = metas or []
        self.fetched: list[int] = []

    def iter_post_meta(self) -> Iterator[PostMeta]:
        # 실제 클라이언트와 같이 최신→과거 순으로 흘려보낸다.
        yield from self._metas

    def fetch_post_html(self, log_no: int) -> str:
        self.fetched.append(log_no)
        queue = self._responses.get(log_no)
        if not queue:
            raise FetchError(f"https://x/{log_no}", attempts=1)
        # 마지막 응답은 이후 호출에서도 반복 사용한다.
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def post_url(self, log_no: int) -> str:
        return f"https://m.blog.naver.com/x/{log_no}"


def _meta(log_no: int) -> PostMeta:
    return PostMeta(
        log_no=log_no, title=f"글 {log_no}", add_date_ms=1692576000000, is_anniversary=False
    )


def _meta_on(log_no: int, d: date) -> PostMeta:
    """주어진 날짜(KST 자정)로 작성 시각을 설정한 PostMeta를 만든다."""
    ms = int(datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=KST).timestamp() * 1000)
    return PostMeta(log_no=log_no, title=f"글 {log_no}", add_date_ms=ms, is_anniversary=False)


def _make_crawler(tmp_path: Path, client: _FakeClient, **kw: object) -> Crawler:
    failures = FailureStore.load(tmp_path)
    return Crawler(client, tmp_path, failures, parse_retries=3, **kw)  # type: ignore[arg-type]


def test_realign_renames_when_seq_drifts(tmp_path: Path) -> None:
    existing = tmp_path / "0500_2023-08-21_제목_123.txt"
    existing.write_text("내용", encoding="utf-8")
    desired = tmp_path / "0499_2023-08-21_제목_123.txt"

    result = _realign(existing, desired)

    assert result == desired
    assert desired.exists()
    assert not existing.exists()


def test_realign_keeps_path_when_already_correct(tmp_path: Path) -> None:
    existing = tmp_path / "0001_2023-08-21_제목_123.txt"
    existing.write_text("내용", encoding="utf-8")

    assert _realign(existing, existing) == existing
    assert existing.exists()


def test_realign_avoids_clobbering_occupied_target(tmp_path: Path) -> None:
    existing = tmp_path / "0500_2023-08-21_제목_123.txt"
    existing.write_text("내용", encoding="utf-8")
    occupied = tmp_path / "0499_2023-08-21_다른글_456.txt"
    occupied.write_text("다른 내용", encoding="utf-8")

    # 목표 이름이 다른 글에 점유돼 있으면 옮기지 않고 기존 경로를 유지한다.
    result = _realign(existing, occupied)

    assert result == existing
    assert existing.exists()
    assert occupied.read_text(encoding="utf-8") == "다른 내용"


def _run_one(crawler: Crawler, meta: PostMeta) -> object:
    return crawler._process_one(1, 1, meta)


def test_failed_post_is_recorded(tmp_path: Path) -> None:
    client = _FakeClient({1: [_BAD_HTML]})
    crawler = _make_crawler(tmp_path, client)
    result = _run_one(crawler, _meta(1))
    assert result.outcome is Outcome.FAILED
    assert 1 in crawler.failures
    # 실패 기록에 사람이 곧장 열어볼 수 있는 글 주소를 함께 남긴다.
    assert crawler.failures.records[0].url == client.post_url(1)
    # parse_retries 만큼 다시 받아본다.
    assert client.fetched == [1, 1, 1]


def test_transient_failure_recovers_within_retries(tmp_path: Path) -> None:
    client = _FakeClient({1: [_BAD_HTML, _BAD_HTML, _VALID_HTML]})
    crawler = _make_crawler(tmp_path, client)
    result = _run_one(crawler, _meta(1))
    assert result.outcome is Outcome.WRITTEN
    assert 1 not in crawler.failures


def test_known_failure_skipped_without_retry(tmp_path: Path) -> None:
    client = _FakeClient({1: [_VALID_HTML]})
    crawler = _make_crawler(tmp_path, client)
    crawler.failures.record(_meta(1), "이전 오류", url=client.post_url(1))
    result = _run_one(crawler, _meta(1))
    assert result.outcome is Outcome.SKIPPED_FAILED
    assert client.fetched == []  # 본문을 받지 않는다


def test_known_failure_retried_and_cleared_when_enabled(tmp_path: Path) -> None:
    client = _FakeClient({1: [_VALID_HTML]})
    crawler = _make_crawler(tmp_path, client, retry_failed=True)
    crawler.failures.record(_meta(1), "이전 오류", url=client.post_url(1))
    result = _run_one(crawler, _meta(1))
    assert result.outcome is Outcome.WRITTEN
    assert 1 not in crawler.failures  # 성공하면 실패 기록 해소


def test_build_plan_reorders_and_reports_progress(tmp_path: Path) -> None:
    # 클라이언트는 최신→과거 순으로 메타를 흘려보낸다.
    client = _FakeClient({}, metas=[_meta(3), _meta(2), _meta(1)])
    crawler = _make_crawler(tmp_path, client)

    collected: list[int] = []
    plan = crawler.build_plan(on_collect=collected.append)

    # on_collect는 모은 누적 건수로 한 건씩 순서대로 호출된다.
    assert collected == [1, 2, 3]
    # 대상은 과거→최근으로 뒤집혀 정렬된다.
    assert [m.log_no for m in plan.targets] == [1, 2, 3]
    assert plan.skipped_anniversary == 0


def test_build_plan_excludes_anniversary_posts(tmp_path: Path) -> None:
    anniversary = PostMeta(log_no=2, title="추억", add_date_ms=0, is_anniversary=True)
    client = _FakeClient({}, metas=[anniversary, _meta(1)])
    crawler = _make_crawler(tmp_path, client)

    collected: list[int] = []
    plan = crawler.build_plan(on_collect=collected.append)

    # 수집 콜백은 거르기 전 전체 건수로 호출되고, 계획에는 '그날의 추억'이 제외된다.
    assert collected == [1, 2]
    assert [m.log_no for m in plan.targets] == [1]
    assert plan.skipped_anniversary == 1


# -- 기간 필터 테스트 (Test-1 ~ Test-6) ------------------------------------

_JAN = date(2023, 1, 1)
_JUN = date(2023, 6, 15)
_AUG = date(2023, 8, 21)
_DEC = date(2023, 12, 31)


def _date_filtered_plan(
    tmp_path: Path,
    *,
    since: date | None = None,
    until: date | None = None,
) -> list[int]:
    """_JAN, _JUN, _AUG, _DEC 날짜 글 4건으로 계획을 세우고 log_no 목록만 돌려준다."""
    # 클라이언트는 최신→과거 순으로 내보낸다.
    metas = [
        _meta_on(4, _DEC),
        _meta_on(3, _AUG),
        _meta_on(2, _JUN),
        _meta_on(1, _JAN),
    ]
    client = _FakeClient({}, metas=metas)
    crawler = _make_crawler(tmp_path, client)
    plan = crawler.build_plan(since=since, until=until)
    return [m.log_no for m in plan.targets]


def test_build_plan_since_excludes_before(tmp_path: Path) -> None:
    # covers: Test-1
    # _JUN(2) 이후만: _JUN, _AUG, _DEC
    ids = _date_filtered_plan(tmp_path, since=_JUN)
    assert ids == [2, 3, 4]


def test_build_plan_until_excludes_after(tmp_path: Path) -> None:
    # covers: Test-2
    # _AUG(3) 이전만: _JAN, _JUN, _AUG
    ids = _date_filtered_plan(tmp_path, until=_AUG)
    assert ids == [1, 2, 3]


def test_build_plan_since_and_until_keeps_range(tmp_path: Path) -> None:
    # covers: Test-3
    # _JUN ~ _AUG 사이: _JUN, _AUG
    ids = _date_filtered_plan(tmp_path, since=_JUN, until=_AUG)
    assert ids == [2, 3]


def test_build_plan_boundary_dates_are_inclusive(tmp_path: Path) -> None:
    # covers: Test-4
    # since=_JAN, until=_DEC → 전체 포함
    ids = _date_filtered_plan(tmp_path, since=_JAN, until=_DEC)
    assert ids == [1, 2, 3, 4]


def test_build_plan_since_equals_until_keeps_exact_day(tmp_path: Path) -> None:
    # covers: Test-5
    # since=until=_AUG → _AUG(3)만
    ids = _date_filtered_plan(tmp_path, since=_AUG, until=_AUG)
    assert ids == [3]


def test_build_plan_no_filter_returns_all(tmp_path: Path) -> None:
    # covers: Test-6
    # 필터 미지정 → 전체
    ids = _date_filtered_plan(tmp_path)
    assert ids == [1, 2, 3, 4]
