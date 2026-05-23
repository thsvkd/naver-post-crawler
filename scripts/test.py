#!/usr/bin/env python3
"""린트·포맷 검사·테스트를 일괄 수행한다(pre-commit hook에서도 사용).

사용:
    python scripts/test.py
"""

from __future__ import annotations

from _common import check, info, require_uv


def main() -> int:
    require_uv()
    info("ruff 린트")
    check(["uv", "run", "ruff", "check", "src", "tests"])
    info("ruff 포맷 검사")
    check(["uv", "run", "ruff", "format", "--check", "src", "tests"])
    info("pytest")
    check(["uv", "run", "pytest"])
    info("모든 검사 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
