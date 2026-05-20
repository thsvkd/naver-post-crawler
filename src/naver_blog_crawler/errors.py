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
