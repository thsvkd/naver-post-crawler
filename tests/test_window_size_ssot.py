"""GUI 창 크기 SSOT 상수 계약 테스트.

``scripts/flet_template.py``(W-8)는 네이티브 러너의 초기 창 크기를 패치할 때 gui.py 를
import 하지 않고 소스를 정규식으로 직접 파싱한다(모듈을 import 하면 flet 까지 딸려
오므로). 그래서 창 크기는 모듈 최상단 상수 한 곳에만 있어야 하고, ``page.window``
대입문은 리터럴이 아니라 그 상수를 참조해야 한다. 이 계약이 깨지면 러너 패치가
조용히 실패하는 것이 아니라 빌드 자체가 죽는다.
"""

from __future__ import annotations

import re
from pathlib import Path

_GUI_PATH = Path(__file__).parents[1] / "src" / "naver_post_crawler" / "gui.py"

# scripts/flet_template.py 가 gui.py 를 파싱할 때 쓰는 것과 같은 정규식이어야 한다.
# 상수 이름·형식을 바꾸면 이 테스트와 그 스크립트를 함께 고쳐야 한다(SSOT 계약).
_CONSTANT_RE = re.compile(r"^_WINDOW_(WIDTH|HEIGHT)\s*=\s*(\d+)\b", re.MULTILINE)
_ASSIGNMENT_RE = re.compile(r"^\s*page\.window\.(width|height)\s*=\s*(.+?)\s*$", re.MULTILINE)

_EXPECTED_CONSTANTS = {"WIDTH": "760", "HEIGHT": "720"}
_EXPECTED_ASSIGNMENTS = {"width": "_WINDOW_WIDTH", "height": "_WINDOW_HEIGHT"}


def test_window_size_is_defined_only_by_module_constants() -> None:
    # covers: Test-2
    source = _GUI_PATH.read_text(encoding="utf-8")

    constants = dict(_CONSTANT_RE.findall(source))
    assert constants == _EXPECTED_CONSTANTS, (
        "gui.py 최상단에 _WINDOW_WIDTH = 760 / _WINDOW_HEIGHT = 720 이 있어야 합니다"
        f"(발견: {constants})."
    )

    assignments = dict(_ASSIGNMENT_RE.findall(source))
    assert assignments == _EXPECTED_ASSIGNMENTS, (
        "page.window.width/height 대입문 우변이 리터럴이 아니라 모듈 상수여야 합니다"
        f"(발견: {assignments})."
    )
