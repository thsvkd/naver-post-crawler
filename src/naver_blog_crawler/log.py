"""로깅 설정.

터미널과 회전 파일 양쪽에 기록한다. ``console``(rich)이 주어지면 진행 화면과
같은 콘솔을 쓰는 핸들러를 붙여 로그를 진행바 위로 흘려보내고, 파일에는 항상
회전 파일 핸들러로 남긴다. 모듈들은 ``logging.getLogger(__name__)``으로
``naver_blog_crawler`` 로거의 자식 로거를 얻어 사용하므로, 여기서 부모 로거에
핸들러를 한 번만 붙인다. 에러는 호출부에서 ``exc_info``와 함께 남겨 원인
트레이스백이 파일에 기록되게 한다.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

LOGGER_NAME = "naver_blog_crawler"

_LOG_FILENAME = "naver-blog-crawler.log"
_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 3
_FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(
    log_dir: Path,
    *,
    level: int = logging.INFO,
    console: Console | None = None,
) -> Path:
    """패키지 로거에 핸들러를 설정하고 로그 파일 경로를 반환한다.

    파일에는 ``level`` 이상을 기록하고, 에러는 호출부의 ``exc_info``로 원인
    트레이스백까지 남긴다. ``console``(rich)이 주어지면 같은 레벨로 터미널에도
    함께 출력한다. 같은 프로세스에서 여러 번 호출돼도 핸들러가 중복되지 않게
    정리한다.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / _LOG_FILENAME

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    _close_handlers(logger)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    logger.addHandler(file_handler)

    if console is not None:
        logger.addHandler(_console_handler(console, level))

    return log_file


def _console_handler(console: Console, level: int) -> logging.Handler:
    """터미널용 rich 핸들러.

    진행 화면(rich Live)과 같은 콘솔을 공유하므로 로그가 진행바 위로 흐르고,
    에러는 보기 좋은 트레이스백으로 펼쳐진다.
    """
    handler = RichHandler(
        console=console,
        level=level,
        show_path=False,
        rich_tracebacks=True,
        log_time_format="[%X]",
    )
    # RichHandler가 시간·레벨을 자체 렌더링하므로 메시지 앞에 로거 이름만 덧붙인다.
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    return handler


def _close_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
