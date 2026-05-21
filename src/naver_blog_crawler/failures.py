"""실패한 글을 영속화해 다음 실행에서 재시도/건너뛰기를 선택할 수 있게 한다.

저장 위치는 출력 디렉토리의 ``.failures.json``. 글의 logNo를 키로 삼아 마지막
오류·시각·시도 횟수를 보관한다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .errors import CrawlerError
from .models import KST, PostMeta

logger = logging.getLogger(__name__)

_STORE_VERSION = 1
_FILENAME = ".failures.json"


@dataclass(frozen=True, slots=True)
class FailureRecord:
    """실패한 글 한 건의 기록."""

    log_no: int
    title: str
    error: str
    failed_at: str
    attempts: int


class FailureStore:
    """실패 기록을 담고 ``.failures.json``에 읽고 쓰는 저장소."""

    def __init__(self, path: Path, records: dict[int, FailureRecord]) -> None:
        self.path = path
        self._records = records

    @classmethod
    def load(cls, out_dir: Path) -> FailureStore:
        """출력 디렉토리에서 실패 기록을 읽는다(없으면 빈 저장소)."""
        path = out_dir / _FILENAME
        records: dict[int, FailureRecord] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise CrawlerError(f"실패 기록 파일이 손상되었습니다: {path}") from exc
            for item in data.get("failures", []):
                record = FailureRecord(**item)
                records[record.log_no] = record
        return cls(path, records)

    def __contains__(self, log_no: int) -> bool:
        return log_no in self._records

    def __len__(self) -> int:
        return len(self._records)

    @property
    def records(self) -> list[FailureRecord]:
        """logNo 순으로 정렬한 실패 기록."""
        return [self._records[key] for key in sorted(self._records)]

    def record(self, meta: PostMeta, error: str) -> None:
        """실패를 기록한다. 이미 있으면 시도 횟수를 누적한다."""
        previous = self._records.get(meta.log_no)
        attempts = previous.attempts + 1 if previous else 1
        self._records[meta.log_no] = FailureRecord(
            log_no=meta.log_no,
            title=meta.title,
            error=error,
            failed_at=datetime.now(KST).isoformat(timespec="seconds"),
            attempts=attempts,
        )

    def clear(self, log_no: int) -> None:
        """성공 등으로 해소된 실패 기록을 제거한다."""
        self._records.pop(log_no, None)

    def save(self) -> None:
        """기록을 파일로 저장한다. 비어 있으면 파일을 지운다."""
        if not self._records:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": _STORE_VERSION,
            "failures": [asdict(record) for record in self.records],
        }
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("실패 기록 %d건 저장: %s", len(self._records), self.path)
