"""크롤링 오케스트레이션.

전체 글 메타데이터를 모아 과거→최근으로 정렬한 뒤, 글마다 본문을 받아
txt로 저장한다. 진행 상황은 글 단위 :class:`PostResult` 이벤트로 흘려보내
호출자(CLI)가 진행률을 표시할 수 있게 한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from .client import NaverBlogClient
from .errors import CrawlerError, ParseError
from .failures import FailureStore
from .models import Post, PostMeta
from .parser import ParsedBody, parse_post_body
from .writer import find_by_log_no, target_path, write_post

# 본문 컨테이너가 없는 응답(스크랩 글 등에서 간헐 발생)은 재요청으로 대부분
# 회복되므로, 한 글당 이 횟수만큼 다시 받아 파싱을 시도한다.
_DEFAULT_PARSE_RETRIES = 3


class Outcome(Enum):
    """글 한 건의 처리 결과."""

    WRITTEN = auto()
    SKIPPED_EXISTING = auto()
    SKIPPED_EMPTY = auto()
    SKIPPED_FAILED = auto()
    FAILED = auto()


@dataclass(frozen=True, slots=True)
class PostResult:
    """글 한 건 처리 후 호출자에게 전달하는 이벤트."""

    seq: int
    total: int
    meta: PostMeta
    outcome: Outcome
    path: Path | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CrawlPlan:
    """수집·정렬을 마친 크롤링 대상 목록."""

    targets: list[PostMeta]
    skipped_anniversary: int

    @property
    def total(self) -> int:
        return len(self.targets)


class Crawler:
    """블로그 전체 글을 txt로 백업한다."""

    def __init__(
        self,
        client: NaverBlogClient,
        out_dir: Path,
        failures: FailureStore,
        *,
        force: bool = False,
        retry_failed: bool = False,
        parse_retries: int = _DEFAULT_PARSE_RETRIES,
    ) -> None:
        self.client = client
        self.out_dir = out_dir
        self.failures = failures
        self.force = force
        self.retry_failed = retry_failed
        self._parse_retries = parse_retries

    def build_plan(self) -> CrawlPlan:
        """전체 메타데이터를 모아 빈 글을 거르고 과거→최근으로 정렬한다."""
        metas = list(self.client.iter_post_meta())
        # API 메타의 thisDayPostInfo로 "N년 전 오늘" 자동 노출 글을 먼저 거른다.
        targets = [m for m in metas if not m.is_anniversary]
        skipped = len(metas) - len(targets)
        # post-list는 최신→과거 순이므로 뒤집어 과거→최근으로 만든다.
        targets.reverse()
        return CrawlPlan(targets=targets, skipped_anniversary=skipped)

    def run(self, plan: CrawlPlan) -> Iterator[PostResult]:
        """계획에 따라 글을 하나씩 저장하며 결과를 흘려보낸다."""
        total = plan.total
        for index, meta in enumerate(plan.targets, start=1):
            yield self._process_one(index, total, meta)

    def _process_one(self, seq: int, total: int, meta: PostMeta) -> PostResult:
        if not self.force:
            existing = find_by_log_no(self.out_dir, meta.log_no)
            if existing is not None:
                # 이미 받은 글이면 본문을 다시 받지 않는다. 글 삭제 등으로 순번이
                # 밀려 파일명이 어긋났다면 현재 순번으로 이름만 갱신해 정렬을 맞춘다.
                path = _realign(existing, target_path(self.out_dir, seq, meta))
                return PostResult(seq, total, meta, Outcome.SKIPPED_EXISTING, path=path)

            # 아직 저장 안 됐고 이전에 실패한 글이면, 재시도 선택에 따라 건너뛴다.
            if not self.retry_failed and meta.log_no in self.failures:
                return PostResult(seq, total, meta, Outcome.SKIPPED_FAILED)

        try:
            body = self._fetch_and_parse(meta.log_no)
        except CrawlerError as exc:
            self.failures.record(meta, str(exc))
            return PostResult(seq, total, meta, Outcome.FAILED, error=str(exc))

        # 성공적으로 받았으면(빈 글 포함) 과거 실패 기록을 해소한다.
        self.failures.clear(meta.log_no)

        # 본문 모듈이 없으면(예: 인용/위젯뿐) 빈 글로 보고 건너뛴다.
        if not body.has_content:
            return PostResult(seq, total, meta, Outcome.SKIPPED_EMPTY)

        post = Post(meta=meta, url=self.client.post_url(meta.log_no), body=body.text)
        path = write_post(self.out_dir, seq, post)
        return PostResult(seq, total, meta, Outcome.WRITTEN, path=path)

    def _fetch_and_parse(self, log_no: int) -> ParsedBody:
        """본문을 받아 파싱한다. 컨테이너 누락(간헐 오류)은 재요청으로 재시도한다."""
        last_exc: ParseError | None = None
        for _attempt in range(self._parse_retries):
            html = self.client.fetch_post_html(log_no)
            try:
                return parse_post_body(html)
            except ParseError as exc:
                last_exc = exc
        assert last_exc is not None
        raise last_exc


def _realign(existing: Path, desired: Path) -> Path:
    """기존 파일을 현재 순번에 맞는 이름으로 옮긴다.

    이름이 이미 맞거나, 목표 이름이 다른 글에 의해 점유되어 있으면(드문 충돌)
    옮기지 않고 기존 경로를 그대로 둔다.
    """
    if existing == desired or desired.exists():
        return existing
    existing.rename(desired)
    return desired
