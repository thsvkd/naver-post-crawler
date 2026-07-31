"""스크립트 공용 헬퍼.

표준 라이브러리만 사용하므로 어느 플랫폼의 어떤 Python에서도 그대로 동작한다.
실제 작업(의존성 설치·실행·빌드)은 ``uv``에 위임하고, 이 파일은 공통 잡일
(저장소 루트 계산, uv 존재 확인, 명령 실행, 메시지 출력)만 담당한다.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import NoReturn

# 콘솔이 UTF-8이 아니면(한국어 Windows 기본 cp949) 이 스크립트들의 안내 문구에 흔한
# 이모지·em-dash(—)·줄임표(…)에서 UnicodeEncodeError로 죽는다. build.py는 자식 프로세스에만
# PYTHONUTF8을 넘기므로 info()/fail() 같은 이 프로세스 자신의 출력은 보호되지 않는다.
# 이 모듈을 import하는 모든 스크립트에 한 번만 적용되도록 여기서 강제한다.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 저장소 루트(scripts/의 부모). 모든 명령은 이 위치에서 실행한다.
REPO_ROOT = Path(__file__).resolve().parent.parent
# sync_version()이 생성하는 파일과 그 안에서 갈아 끼울 줄.
_INIT_PATH = REPO_ROOT / "src" / "naver_post_crawler" / "__init__.py"
_VERSION_LINE_RE = re.compile(r'^__version__\s*=\s*["\'][^"\']*["\']', re.MULTILINE)


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


def run(
    command: list[str],
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> int:
    """명령을 실행하고 종료 코드를 돌려준다.

    ``env``를 주면 자식 프로세스 환경 변수를 그 값으로 대체한다(None이면 상속).
    ``cwd``를 주면 그 디렉터리에서 실행한다(None이면 저장소 루트).
    """
    return subprocess.run(command, cwd=cwd or REPO_ROOT, env=env).returncode


def check(
    command: list[str],
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> None:
    """:func:`run`과 같으나, 종료 코드가 0이 아니면 즉시 종료한다."""
    code = run(command, env=env, cwd=cwd)
    if code != 0:
        fail(f"명령 실패(exit {code}): {' '.join(command)}")


def pyproject_version() -> str:
    """pyproject.toml의 ``[project].version``을 읽는다. 앱 버전의 SSoT다."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    try:
        return data["project"]["version"]
    except KeyError:
        fail("pyproject.toml에서 [project].version을 찾지 못했습니다.")


def sync_version() -> str:
    """pyproject.toml의 버전을 ``src/naver_post_crawler/__init__.py``에 반영한다.

    ``flet build``는 앱을 site-packages에 정식 설치하지 않고 ``src/``를 그대로 복사하므로
    배포된 앱 안에서 ``importlib.metadata``로 버전을 읽을 수 없다. 그래서 ``__init__.py``의
    ``__version__``은 사람이 고치는 값이 아니라 이 함수가 pyproject.toml로부터 만들어 내는
    생성물이다 — 버전을 바꾸려면 pyproject.toml만 고치고 이 함수를 다시 실행한다.
    """
    version = pyproject_version()
    text = _INIT_PATH.read_text(encoding="utf-8")
    new_text, n = _VERSION_LINE_RE.subn(f'__version__ = "{version}"', text)
    if n != 1:
        fail(f"{_INIT_PATH}에서 __version__ 줄을 정확히 하나 찾지 못했습니다.")
    if new_text != text:
        _INIT_PATH.write_text(new_text, encoding="utf-8")
        info(f"버전 동기화: __init__.py -> {version}")
    return version
