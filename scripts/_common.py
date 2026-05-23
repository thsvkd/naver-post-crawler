"""스크립트 공용 헬퍼.

표준 라이브러리만 사용하므로 어느 플랫폼의 어떤 Python에서도 그대로 동작한다.
실제 작업(의존성 설치·실행·빌드)은 ``uv``에 위임하고, 이 파일은 공통 잡일
(저장소 루트 계산, uv 존재 확인, 명령 실행, 메시지 출력)만 담당한다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

# 저장소 루트(scripts/의 부모). 모든 명령은 이 위치에서 실행한다.
REPO_ROOT = Path(__file__).resolve().parent.parent


def info(message: str) -> None:
    """진행 상황을 한 줄로 출력한다."""
    print(f"==> {message}", flush=True)


def fail(message: str) -> NoReturn:
    """오류 메시지를 stderr에 출력하고 종료 코드 1로 종료한다."""
    print(f"오류: {message}", file=sys.stderr)
    raise SystemExit(1)


def require_uv() -> None:
    """uv가 PATH에 있는지 확인한다. 없으면 안내 후 종료한다."""
    if shutil.which("uv") is None:
        fail("uv가 설치되어 있지 않습니다. https://docs.astral.sh/uv/ 를 참고하세요.")


def run(command: list[str]) -> int:
    """저장소 루트에서 명령을 실행하고 종료 코드를 돌려준다."""
    return subprocess.run(command, cwd=REPO_ROOT).returncode


def check(command: list[str]) -> None:
    """:func:`run`과 같으나, 종료 코드가 0이 아니면 즉시 종료한다."""
    code = run(command)
    if code != 0:
        fail(f"명령 실패(exit {code}): {' '.join(command)}")
