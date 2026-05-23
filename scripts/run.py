#!/usr/bin/env python3
"""크롤러를 실행한다. 어느 플랫폼에서도 동작한다.

인자 없이 실행하거나 ``--gui`` 를 붙이면 GUI 창 모드로 실행한다(블로그 아이디는
창에서 입력하므로 인자가 필요 없다). 블로그 아이디/URL을 인자로 주면 CLI 모드로,
그 인자를 그대로 ``naver-blog-crawler`` CLI에 전달한다.

사용 예:
    python scripts/run.py                       # GUI 창
    python scripts/run.py --gui                 # GUI 창(명시적)
    python scripts/run.py winter9377            # CLI 백업
    python scripts/run.py winter9377 --limit 10 -o output
"""

from __future__ import annotations

import sys

from _common import require_uv, run


def main() -> int:
    require_uv()
    args = sys.argv[1:]

    # 인자 없이 실행하거나 --gui면 GUI 창 모드. flet은 gui extra라 --extra gui로 띄운다.
    # (GUI에서는 블로그 아이디를 입력칸에 적으므로 인자가 필요 없다.)
    if not args or "--gui" in args:
        return run(["uv", "run", "--extra", "gui", "naver-blog-crawler-gui"])

    # 인자가 있으면 CLI 모드 — BLOG(블로그 아이디/URL)와 옵션을 그대로 전달한다.
    return run(["uv", "run", "naver-blog-crawler", *args])


if __name__ == "__main__":
    raise SystemExit(main())
