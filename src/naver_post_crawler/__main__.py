"""``python -m naver_post_crawler`` 진입점.

기본은 GUI를 띄운다. 헬퍼 플래그(:data:`~naver_post_crawler.cookie_login.HELPER_FLAG`)가
있으면 ``gui.main()``이 로그인 웹뷰 헬퍼로 분기한다. 개발 실행에서 로그인 헬퍼
서브프로세스를 이 경로로 재실행한다(``cookie_login._helper_command``).
"""

from .gui import main

main()
