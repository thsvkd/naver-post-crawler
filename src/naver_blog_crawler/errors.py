"""크롤러 전역에서 사용하는 예외 정의."""

from __future__ import annotations


class CrawlerError(Exception):
    """크롤러 기반 예외."""


class FetchError(CrawlerError):
    """네트워크 요청이 재시도 이후에도 실패했을 때 발생."""

    def __init__(self, url: str, *, attempts: int, cause: Exception | None = None) -> None:
        self.url = url
        self.attempts = attempts
        self.cause = cause
        message = f"요청 실패 ({attempts}회 시도): {url}"
        if cause is not None:
            message = f"{message} — {cause!r}"
        super().__init__(message)


class ParseError(CrawlerError):
    """HTML에서 기대한 구조(예: se-main-container)를 찾지 못했을 때 발생."""


class InvalidBlogReference(CrawlerError):
    """입력값에서 블로그 아이디를 인식하지 못했을 때 발생."""


class BlogNotFound(CrawlerError):
    """존재하지 않는 블로그를 요청했을 때 발생.

    형식은 유효하지만 실제로 없는 아이디(post-list가 404 ``not_exist_blog`` 반환)다.
    네트워크 일시 장애와 달리 재시도해도 의미가 없으므로 즉시 중단시킨다.
    """

    def __init__(self, blog_id: str) -> None:
        self.blog_id = blog_id
        super().__init__(f"존재하지 않는 블로그입니다: {blog_id}")
