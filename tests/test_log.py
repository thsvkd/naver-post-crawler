"""로깅 설정 테스트."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

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


def test_console_handler_added_when_console_given(tmp_path: Path) -> None:
    setup_logging(tmp_path, console=Console())
    handlers = logging.getLogger(LOGGER_NAME).handlers
    # 파일 핸들러 + 콘솔(rich) 핸들러 두 개가 붙는다.
    assert len(handlers) == 2
    assert any(isinstance(handler, RichHandler) for handler in handlers)


def test_console_setup_is_idempotent(tmp_path: Path) -> None:
    # GUI는 실행할 때마다 setup_logging을 다시 부르므로, 콘솔 경로에서도 핸들러가
    # 누적되지 않아야 한다.
    setup_logging(tmp_path, console=Console())
    setup_logging(tmp_path, console=Console())
    handlers = logging.getLogger(LOGGER_NAME).handlers
    assert len(handlers) == 2
    assert sum(isinstance(handler, RichHandler) for handler in handlers) == 1


def test_no_console_handler_by_default(tmp_path: Path) -> None:
    setup_logging(tmp_path)
    handlers = logging.getLogger(LOGGER_NAME).handlers
    # console 미지정 시 파일 핸들러만 붙어 콘솔(진행 화면)을 건드리지 않는다.
    assert len(handlers) == 1
    assert not any(isinstance(handler, RichHandler) for handler in handlers)


def test_error_with_exc_info_logs_traceback(tmp_path: Path) -> None:
    log_file = setup_logging(tmp_path, level=logging.INFO)

    try:
        raise ValueError("원인 예외")
    except ValueError:
        logging.getLogger(f"{LOGGER_NAME}.child").error("실패", exc_info=True)
    for handler in logging.getLogger(LOGGER_NAME).handlers:
        handler.flush()

    content = log_file.read_text(encoding="utf-8")
    # exc_info로 남긴 에러는 원인 트레이스백까지 파일에 기록된다.
    assert "Traceback" in content
    assert "원인 예외" in content
