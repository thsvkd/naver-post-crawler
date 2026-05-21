"""크롤러 보조 로직 및 실패 기록·재시도 통합 테스트."""

from __future__ import annotations

from pathlib import Path

from naver_blog_crawler.crawler import Crawler, Outcome, _realign
from naver_blog_crawler.errors import FetchError
from naver_blog_crawler.failures import FailureStore
from naver_blog_crawler.models import PostMeta

_VALID_HTML = (
    '<div class="se-main-container">'
    '<div class="se-component se-text"><div class="se-text-paragraph">본문</div></div>'
    "</div>"
)
_BAD_HTML = "<html><body>컨테이너 없음</body></html>"


class _FakeClient:
    """logNo별로 정해진 HTML 시퀀스를 돌려주는 가짜 클라이언트."""

    def __init__(self, responses: dict[int, list[str]]) -> None:
        self._responses = responses
        self.fetched: list[int] = []

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
    crawler.failures.record(_meta(1), "이전 오류")
    result = _run_one(crawler, _meta(1))
    assert result.outcome is Outcome.SKIPPED_FAILED
    assert client.fetched == []  # 본문을 받지 않는다


def test_known_failure_retried_and_cleared_when_enabled(tmp_path: Path) -> None:
    client = _FakeClient({1: [_VALID_HTML]})
    crawler = _make_crawler(tmp_path, client, retry_failed=True)
    crawler.failures.record(_meta(1), "이전 오류")
    result = _run_one(crawler, _meta(1))
    assert result.outcome is Outcome.WRITTEN
    assert 1 not in crawler.failures  # 성공하면 실패 기록 해소
