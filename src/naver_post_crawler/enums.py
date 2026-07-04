"""스마트에디터(SmartEditor) 컴포넌트 식별에 쓰이는 상수."""

from __future__ import annotations

from enum import StrEnum

# 본문 컨테이너 셀렉터 (모바일 PostView 기준)
MAIN_CONTAINER_SELECTOR = ".se-main-container"

# 구버전 스마트에디터(SE 3.0) 본문 영역 마커.
# se-main-container(스마트에디터 ONE)가 없는 옛 글(주로 외부 글을 가져온 [공유]
# 글)은 하이픈식이 아니라 언더스코어식 클래스(se_component 등)를 쓰며, 본문이
# 여러 se_doc_viewer 블록에 나뉘어 담긴다. 이 둘을 모두 감싸는 컨테이너의 마커다.
LEGACY_CONTAINER_SELECTOR = ".se3_view"

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


class LegacyModuleClass(StrEnum):
    """구버전 SE 3.0 se_component의 종류를 나타내는 CSS 클래스.

    스마트에디터 ONE의 :class:`ModuleClass`(하이픈식)에 대응하는 언더스코어식
    클래스다. 본문 외 요소(제목)는 ``DOCUMENT_TITLE``로 식별해 걸러낸다.
    """

    TEXT = "se_paragraph"
    IMAGE = "se_image"
    OGLINK = "se_oglink"
    QUOTATION = "se_quotation"
    # 글 제목 컴포넌트. 메타데이터로 따로 다루므로 본문에서는 제외한다.
    DOCUMENT_TITLE = "se_documentTitle"


# 구버전에서 실제 사용자 콘텐츠로 간주하는 모듈 집합.
# SE-ONE의 CONTENT_MODULE_CLASSES와 같은 역할로, '내용 없는 글' 판정을
# 두 파서 경로에서 동일한 기준으로 한다.
LEGACY_CONTENT_MODULE_CLASSES: frozenset[str] = frozenset(
    {
        LegacyModuleClass.TEXT,
        LegacyModuleClass.IMAGE,
        LegacyModuleClass.OGLINK,
        LegacyModuleClass.QUOTATION,
    }
)
