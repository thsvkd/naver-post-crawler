#!/usr/bin/env python3
"""린트·포맷 검사·테스트를 일괄 수행한다(pre-commit hook에서도 사용).

사용:
    python scripts/test.py              # 전체 검사(ruff 린트 + 포맷 검사 + pytest)
    python scripts/test.py -k login     # 인자를 주면 그대로 pytest로만 전달한다
    python scripts/test.py tests/test_cookie.py -v

설명:
    - 인자가 없으면 커밋 전 게이트와 같은 전체 검사를 돈다. 인자를 하나라도 주면 **pytest만**
      그 인자로 실행한다 — 특정 테스트를 반복해 돌리며 고치는 중에 린트까지 매번 도는 것은
      방해가 되기 때문이다. hook은 인자 없이 부르므로 게이트는 그대로 유지된다.
    - 린트 대상에 scripts/도 넣는다. 빌드·배포 로직이 여기 있고 그 버그는 릴리스 사고로
      이어진다(테스트도 tests/에서 이 디렉터리를 import해 검증한다).
"""

from __future__ import annotations

import sys

from _common import check, info, require_uv, run

_LINT_TARGETS = ["src", "tests", "scripts"]


def main() -> int:
    require_uv()

    forwarded = sys.argv[1:]
    if forwarded:
        return run(["uv", "run", "pytest", *forwarded])

    info("ruff 린트")
    check(["uv", "run", "ruff", "check", *_LINT_TARGETS])
    info("ruff 포맷 검사")
    check(["uv", "run", "ruff", "format", "--check", *_LINT_TARGETS])
    info("pytest")
    check(["uv", "run", "pytest"])
    info("모든 검사 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
