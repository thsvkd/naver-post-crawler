"""로깅 설정 테스트."""

from __future__ import annotations

import logging
from pathlib import Path

from naver_blog_crawler.log import LOGGER_NAME, setup_logging


def test_setup_creates_log_file_and_writes(tmp_path: Path) -> None:
    log_file = setup_logging(tmp_path, level=logging.INFO)
    assert log_file.exists()

    logging.getLogger(f"{LOGGER_NAME}.child").info("테스트 메시지")
    for handler in logging.getLogger(LOGGER_NAME).handlers:
        handler.flush()

    assert "테스트 메시지" in log_file.read_text(encoding="utf-8")


def test_setup_is_idempotent(tmp_path: Path) -> None:
    setup_logging(tmp_path)
    setup_logging(tmp_path)
    # 반복 호출해도 핸들러가 중복되지 않아야 한다.
    assert len(logging.getLogger(LOGGER_NAME).handlers) == 1


def test_setup_respects_level(tmp_path: Path) -> None:
    setup_logging(tmp_path, level=logging.WARNING)
    assert logging.getLogger(LOGGER_NAME).level == logging.WARNING
