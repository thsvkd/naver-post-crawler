"""스마트에디터(SmartEditor) 컴포넌트 식별에 쓰이는 상수."""

from __future__ import annotations

from enum import StrEnum

# 본문 컨테이너 셀렉터 (모바일 PostView 기준)
MAIN_CONTAINER_SELECTOR = ".se-main-container"

# 본문 영역의 끝을 표시하는 광고 포털 엘리먼트 id.
# 이 엘리먼트 이후의 노드는 본문으로 취급하지 않는다.
AD_PORTAL_ID = "ad-bottom-portal"


class ModuleClass(StrEnum):
    """se-component의 종류를 나타내는 CSS 클래스 접미사.

    selectolax의 클래스 매칭에 사용한다. 네이버가 모듈 단위로 본문을
    구성하므로, 요소 개수 같은 휴리스틱 대신 모듈 종류로 의미를 판별한다.
    """

    TEXT = "se-text"
    IMAGE = "se-image"
    IMAGE_GROUP = "se-imageGroup"
    OGLINK = "se-oglink"
    QUOTATION = "se-quotation"
    VIDEO = "se-video"
    OEMBED = "se-oembed"
    CODE = "se-code"
    TABLE = "se-table"
    HORIZONTAL_LINE = "se-horizontalLine"
    # "N년 전 오늘 / 그날의 추억" 자동 노출 위젯. 실제 본문이 아니다.
    ANNIVERSARY = "se-anniversarySection"


# 실제 사용자 콘텐츠로 간주하는 모듈 집합.
# 이 집합에 속한 모듈이 하나도 없으면 '내용 없는 글'로 판정한다.
CONTENT_MODULE_CLASSES: frozenset[str] = frozenset(
    {
        ModuleClass.TEXT,
        ModuleClass.IMAGE,
        ModuleClass.IMAGE_GROUP,
        ModuleClass.OGLINK,
        ModuleClass.QUOTATION,
        ModuleClass.VIDEO,
        ModuleClass.OEMBED,
        ModuleClass.CODE,
        ModuleClass.TABLE,
    }
)
