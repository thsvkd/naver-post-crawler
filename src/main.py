"""flet build 진입점.

``flet build``는 앱 루트(``src/``)에서 이 모듈을 ``__main__`` 스크립트로 실행하므로,
패키지 내부 상대 임포트가 깨지지 않도록 절대 임포트로 GUI를 불러와 그대로 실행한다.
개발 중 실행(``naver-post-crawler-gui``)은 :func:`naver_post_crawler.gui.main`을
직접 진입점으로 쓰며, 이 파일은 네이티브 빌드 전용 얇은 셔임이다.
"""

from naver_post_crawler.gui import main

main()
