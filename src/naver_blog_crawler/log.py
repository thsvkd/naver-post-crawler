"""로깅 설정.

진행 화면(rich Live)과 충돌하지 않도록 콘솔이 아닌 회전 파일에 기록한다.
모듈들은 ``logging.getLogger(__name__)``으로 ``naver_blog_crawler`` 로거의
자식 로거를 얻어 사용하므로, 여기서 부모 로거에 핸들러를 한 번만 붙인다.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "naver_blog_crawler"

_LOG_FILENAME = "naver-blog-crawler.log"
_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 3
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(log_dir: Path, level: int = logging.INFO) -> Path:
    """패키지 로거에 회전 파일 핸들러를 설정하고 로그 파일 경로를 반환한다.

    같은 프로세스에서 여러 번 호출돼도 핸들러가 중복되지 않게 정리한다.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / _LOG_FILENAME

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    _close_handlers(logger)

    handler = RotatingFileHandler(
        log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)
    return log_file


def _close_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
