"""크롤러가 다루는 데이터 구조."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# 네이버 블로그의 작성 시각은 KST 기준으로 표기한다.
KST = timezone(timedelta(hours=9), name="KST")


@dataclass(frozen=True, slots=True)
class PostMeta:
    """post-list API가 돌려주는 글 한 건의 메타데이터."""

    log_no: int
    title: str
    add_date_ms: int
    is_anniversary: bool

    @property
    def written_at(self) -> datetime:
        """작성 시각(KST)."""
        return datetime.fromtimestamp(self.add_date_ms / 1000, tz=KST)

    @property
    def date_str(self) -> str:
        """파일명·헤더에 쓰는 YYYY-MM-DD 형식 날짜."""
        return self.written_at.strftime("%Y-%m-%d")


@dataclass(frozen=True, slots=True)
class Post:
    """본문까지 추출한 글 한 건."""

    meta: PostMeta
    url: str
    body: str
