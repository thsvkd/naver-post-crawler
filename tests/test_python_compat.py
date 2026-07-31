"""배포 인터프리터(CPython 3.12) 문법 호환성 회귀 테스트.

``flet build``가 번들에 임베드하는 인터프리터는 CPython 3.12다(핸드오프 §3-1, 참조
프로젝트 산출물 tarball 실측). 개발 머신 인터프리터에서는 3.14 전용 문법(PEP 758의
괄호 없는 except 튜플 등)이 그대로 통과하므로, "개발에서는 되는데 배포본이 import
단계에서 죽는" 실패는 개발 중에 절대 재현되지 않는다. 그래서 ``src/`` 전체를 3.12
인터프리터로 직접 compile 해 본다.

3.12 인터프리터를 못 찾으면 skip 이 아니라 실패로 처리한다 — skip 은 아무도 돌리지
않은 테스트를 green 으로 보이게 만들어 이 회귀를 그대로 통과시킨다.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[1]
_SRC = _REPO_ROOT / "src"

# flet build 가 임베드하는 인터프리터 계열(참조 산출물 실측: cpython-3.12.9 tarball).
# pyproject.toml 의 requires-python 하한과 같은 값을 유지한다.
_TARGET_PYTHON = "3.12"

# 대상 인터프리터 안에서 실행할 프로그램. src/ 아래 모든 .py 를 compile 하고
# SyntaxError 를 "파일:줄: 메시지" 한 줄씩 보고한 뒤, 마지막 줄에 컴파일한 파일 수를
# 남긴다(대상 파일이 0개인데 조용히 통과하는 것을 막기 위한 계수).
_COMPILE_PROGRAM = """
import pathlib
import sys

paths = sorted(pathlib.Path(sys.argv[1]).rglob("*.py"))
errors = []
for path in paths:
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as exc:
        errors.append(f"{path}:{exc.lineno}: {exc.msg}")
for line in errors:
    print(line)
print(f"compiled={len(paths)}")
sys.exit(1 if errors else 0)
"""


def _target_interpreter() -> str:
    """uv 가 관리하는 CPython 3.12 실행 파일 경로를 돌려준다.

    찾지 못하면 이유를 담아 실패한다(``uv python install 3.12`` 로 복구 가능).
    """
    uv = shutil.which("uv")
    assert uv is not None, "uv 를 PATH 에서 찾지 못했습니다. https://docs.astral.sh/uv/ 참고."

    found = subprocess.run(
        [uv, "python", "find", _TARGET_PYTHON],
        capture_output=True,
        text=True,
    )
    assert found.returncode == 0, (
        f"CPython {_TARGET_PYTHON} 인터프리터를 찾지 못했습니다"
        f"(`uv python install {_TARGET_PYTHON}` 으로 설치하세요).\n{found.stderr}"
    )
    path = found.stdout.strip()
    assert path, f"`uv python find {_TARGET_PYTHON}` 이 빈 경로를 돌려줬습니다.\n{found.stderr}"

    # 찾은 것이 정말 3.12 인지 확인한다 — 다른 버전을 잡아 놓고 통과하면 무의미하다.
    reported = subprocess.run(
        [path, "-c", "import sys; print('.'.join(str(n) for n in sys.version_info[:2]))"],
        capture_output=True,
        text=True,
    )
    assert reported.stdout.strip() == _TARGET_PYTHON, (
        f"{path} 의 버전이 {_TARGET_PYTHON} 가 아닙니다: {reported.stdout.strip()!r}"
    )
    return path


def test_src_compiles_on_deploy_interpreter() -> None:
    # covers: Test-1
    python312 = _target_interpreter()

    result = subprocess.run(
        [python312, "-c", _COMPILE_PROGRAM, str(_SRC)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"CPython {_TARGET_PYTHON} 에서 컴파일되지 않는 파일이 있습니다"
        f"(배포본이 import 단계에서 죽습니다):\n{result.stdout}{result.stderr}"
    )
    compiled = int(result.stdout.strip().splitlines()[-1].removeprefix("compiled="))
    assert compiled > 0, f"{_SRC} 아래에서 컴파일 대상 .py 를 하나도 찾지 못했습니다."
