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
from .errors import CrawlerError
from .models import Post, PostMeta
from .parser import parse_post_body
from .writer import find_existing, write_post


class Outcome(Enum):
    """글 한 건의 처리 결과."""

    WRITTEN = auto()
    SKIPPED_EXISTING = auto()
    SKIPPED_EMPTY = auto()
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

    def __init__(self, client: NaverBlogClient, out_dir: Path, *, force: bool = False) -> None:
        self.client = client
        self.out_dir = out_dir
        self.force = force

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
            existing = find_existing(self.out_dir, seq)
            if existing is not None:
                return PostResult(seq, total, meta, Outcome.SKIPPED_EXISTING, path=existing)

        try:
            html = self.client.fetch_post_html(meta.log_no)
            body = parse_post_body(html)
        except CrawlerError as exc:
            return PostResult(seq, total, meta, Outcome.FAILED, error=str(exc))

        # 본문 모듈이 없으면(예: 인용/위젯뿐) 빈 글로 보고 건너뛴다.
        if not body.has_content:
            return PostResult(seq, total, meta, Outcome.SKIPPED_EMPTY)

        post = Post(meta=meta, url=self.client.post_url(meta.log_no), body=body.text)
        path = write_post(self.out_dir, seq, post)
        return PostResult(seq, total, meta, Outcome.WRITTEN, path=path)
