#!/usr/bin/env python3
"""개발 환경을 준비한다. 어느 플랫폼에서도 동작한다.

의존성을 동기화하고(uv sync) git pre-commit hook을 설치한다.

사전 준비(일회성): uv  (https://docs.astral.sh/uv/)

사용:
    python scripts/setup.py
"""

from __future__ import annotations

from _common import REPO_ROOT, check, info, require_uv

# git pre-commit hook 본문. Git은 Windows에서도 번들된 sh로 hook을 실행하므로
# bash hook이 그대로 동작한다. 커밋 전에 scripts/test.py(린트·포맷·테스트)를 강제한다.
_PRE_COMMIT_HOOK = """#!/usr/bin/env bash
set -euo pipefail
exec uv run python "$(git rev-parse --show-toplevel)/scripts/test.py"
"""


def install_pre_commit_hook() -> None:
    """저장소가 git 저장소면 pre-commit hook을 설치한다."""
    git_dir = REPO_ROOT / ".git"
    if not git_dir.exists():
        return
    info("pre-commit hook 설치")
    hook_path = git_dir / "hooks" / "pre-commit"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    # Git hook은 LF 줄바꿈이어야 sh가 올바르게 해석한다(newline="\n"로 변환 방지).
    hook_path.write_text(_PRE_COMMIT_HOOK, encoding="utf-8", newline="\n")
    # 실행 권한 부여(POSIX). Windows에서는 무의미하지만 무해하다.
    hook_path.chmod(0o755)


def main() -> int:
    require_uv()
    info("의존성 동기화 (uv sync)")
    check(["uv", "sync"])
    install_pre_commit_hook()
    info("완료. 'python scripts/run.py' 로 GUI를, "
         "'python scripts/run.py <블로그아이디>' 로 CLI 백업을 실행할 수 있습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
