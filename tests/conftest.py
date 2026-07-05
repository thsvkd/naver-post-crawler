"""테스트 공용 설정.

scripts/ 아래 빌드 스크립트(build.py 등)는 src-layout 패키지가 아니라 단독 실행
스크립트다. 그 스크립트가 하는 ``from _common import ...``를 테스트에서도 해석할 수
있도록 scripts/를 import 경로에 추가한다(우선순위를 낮추려 append). 스크립트 모듈
자체는 test에서 파일 경로로 직접 로드한다 — 최상위 이름 ``build``가 저장소의 build/
출력 폴더(암시적 네임스페이스 패키지)와 충돌하는 것을 피하기 위해서다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.append(str(_SCRIPTS))
