"""네이버 블로그·카페의 글을 과거→최근 순으로 txt로 백업하는 크롤러."""

# 런타임에서 접근 가능한 버전 SSoT. pyproject.toml의 [project].version과 반드시
# 일치해야 하며(tests/test_version.py가 강제), 릴리스 시 두 곳을 함께 올린다.
# 패키징된 onefile(exe)에는 .dist-info 메타데이터가 없어 importlib.metadata가
# 실패할 수 있으므로 하드코딩한다.
__version__ = "0.1.0"
