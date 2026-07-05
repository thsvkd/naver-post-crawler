# 핸드오프 — 앱 내 웹뷰 네이버 로그인 (Phase 1)

> R1에서 인간과 합의한 계획의 고충실도 외부 문서. R2(테스트 코드 작성)·구현·리뷰
> 에이전트의 **입력**이다. 새 케이스를 추가·재해석하지 말고 이 문서의 테스트 케이스
> 리스트를 1:1로 인코딩한다.

## 1. 배경 / PRD

- **문제**: 카페 로그인 쿠키 획득이 5단계(크롬 확장 설치 → 네이버 로그인 → 확장으로
  Export → `*_cookies.txt` 저장 → GUI에서 파일 선택). 외부 도구 의존 + 마찰이 큼.
- **목표**: GUI 고급 옵션에 **"네이버 로그인" 버튼** 하나 → 네이티브 웹뷰 창에서
  사용자가 직접 로그인 → **HttpOnly 세션 쿠키(NID_AUT/NID_SES 포함)를 자동 수거 →
  기존 저장 경로에 저장**. 확장·파일·F12 제거.
- **핵심 제약**: `NID_AUT`는 **HttpOnly** 쿠키라 JS `document.cookie`로 못 읽는다.
  반드시 웹뷰 엔진의 **네이티브 쿠키 저장소**에서 읽어야 한다.
- **범위(이번 라운드)**: Windows, 개발 실행 + `flet pack`. (`flet build` 패키징은 Phase 2)
- **비목표**: 자동 로그인/자격증명 저장 안 함(네이버 약관·캡차·기기인증). 기존
  파일/직접입력 방식은 **폴백으로 유지**(제거하지 않는다).

## 2. 핵심 결정 (인간 합의)

1. **아키텍처**: pywebview를 **별도 헬퍼 서브프로세스**로 실행한다. Flet(serious_python)은
   CPython을 Flutter와 한 프로세스에 내장해 진짜 메인 스레드를 Flutter가 점유하므로,
   메인 스레드를 요구하는 pywebview를 인프로세스로 띄울 수 없다.
2. **부모↔헬퍼 계약**: 헬퍼가 수거 결과를 **stdout에 JSON 한 줄**로 반환한다.
   ```json
   {"status": "captured", "cookies": [{"name": "NID_AUT", "value": "…", "domain": ".naver.com"}, …]}
   ```
   `status`는 `captured` | `timeout` | `error`. 부모는 stdout을 파싱해 헤더 문자열을 만든다.
3. **쿠키 범위**: `.naver.com`(하위 도메인 포함) 쿠키 **전부** 저장 — 기존 파일 경로의
   `_select_naver`와 동일 규칙.
4. **창 닫힘**: `loaded` 이벤트 + 폴링으로 `NID_AUT` 등장 즉시 수거하고 창 **자동 종료**.
   `TIMEOUT_S`(기본 300초) 내 로그인 없으면 `timeout`.
5. **프로필 프라이버시**: 헬퍼 웹뷰는 `private_mode=True`로 열어 로그인 세션을 디스크에
   영속하지 않는다(인메모리, 수거 후 종료).
6. **코드 재사용(SSoT)**: `cookie.py`의 naver 필터 + 조인을 `format_cookie_header()`로
   추출해 **파일 경로와 웹뷰 경로가 공유**한다. `save_cookie`/`load_cookie`/소비 경로
   (`_build_source`)는 변경하지 않는다.
7. **진입 디스패치**: dev·flet pack 모두 `gui.main()`을 통과하므로 헬퍼 플래그를 거기서
   처리한다. frozen(`sys.frozen`) 여부로 재실행 커맨드를 분기(자세한 배선은 구현 재량,
   E2E로 검증).

## 3. 기각한 대안

- `ft.WebView`: Windows/Linux 미지원 + 쿠키 읽기 API 자체가 없음.
- 설치된 브라우저 쿠키 DB 추출(browser_cookie3): 최신 Chrome/Edge App-Bound 암호화로
  취약, 크롬 변화에 계속 쫓김.
- Playwright/CDP: Chromium 번들 ~150–280MB로 배포 모델에 부적합.
- 인프로세스 pywebview: Flet이 메인 스레드 점유 → 불가.

## 4. 스파이크 실증 (근거)

버리는 프로토타입으로 이 Win11에서 실측 완료:
- pywebview 6.2.1 + WebView2로 네이버 로그인 후 `NID_AUT`(len=64)·`NID_SES`(len=604)
  **HttpOnly 쿠키 추출 성공**, 최종 URL `https://www.naver.com/`, naver 쿠키 10개 수거.
- `get_cookies()` 반환은 **`list[http.cookies.SimpleCookie]`**(조사가 추정한
  `http.cookiejar.Cookie` 아님). 각 항목은 `.items()` → `(name, Morsel)`이고 Morsel은
  `.value`, `["domain"]`을 가진다. → `normalize_cookies`가 이 형태를 처리해야 한다.

## 5. 테스트 케이스 리스트 (인수 기준)

> `covers: Test-N` 태그를 각 테스트에 단다. E2E-1은 자동화 불가(수동/실측, R3).

### A. 포맷/필터 계층 — `src/naver_post_crawler/cookie.py`
- **Test-1**: `format_cookie_header(triples)`가 `.naver.com`(하위 도메인 포함) 쿠키만
  골라(이름 기준 중복 제거) `"name=value; name=value"`로 조인한다. 비-naver 도메인은
  제외한다. (기존 `_select_naver` 동작 보존)
- **Test-2**: naver 쿠키가 하나도 없으면 `format_cookie_header`는 **빈 문자열**을 돌려주고,
  `parse_cookie_file`은 그 경우 기존대로 `InvalidCookieFile`을 던진다(파일 경로 회귀 없음).

### B. 헬퍼 쿠키 정규화 — `src/naver_post_crawler/cookie_login.py`
- **Test-3**: `normalize_cookies(raw)`가 pywebview의 `list[SimpleCookie]`(각 SimpleCookie가
  `name → Morsel(value, domain)`)를 `(name, value, domain)` 삼중쌍 리스트로 올바로
  변환한다(도메인 포함). 빈 입력 → 빈 리스트.

### C. 부모↔헬퍼 계약 파서 — `src/naver_post_crawler/cookie_login.py`
- **Test-4**: `parse_helper_output(returncode=0, stdout=<status:captured, NID_AUT/NID_SES 포함>)`
  → naver 헤더 문자열(`format_cookie_header` 결과)을 돌려준다.
- **Test-5**: `parse_helper_output(0, <status:timeout 또는 NID_AUT 없음>)` → `None`(로그인 없음).
- **Test-6**: `parse_helper_output`가 **returncode != 0**, 또는 **빈/손상 stdout**(JSON 아님)
  → `None`을 돌려주고 예외를 던지지 않는다(부모가 크래시하지 않음).

### D. GUI 배선 — `src/naver_post_crawler/gui.py` (test_gui.py 스타일)
- **Test-7**: 고급 옵션에 "네이버 로그인" 버튼이 존재하고 로그인 핸들러에 연결된다.
- **Test-8**: 로그인 핸들러는 캡처를 **UI 스레드에서 직접 실행하지 않고** `page.run_thread`로
  오프스레드 실행한다. 오프스레드 작업은 헤더를 얻으면 `save_cookie(header)` 호출 +
  성공 상태로 갱신하고, `None`이면 **저장하지 않고**(기존 쿠키 보존) 실패 상태로 갱신한다.

### E. E2E 실측 (R3, 수동)
- **E2E-1**: "네이버 로그인" 클릭 → 웹뷰 로그인 → 창 자동 종료 → "저장된 쿠키: 있음 ✓"
  → 로그인 필요 카페 게시판 백업 성공.

## 6. 구현 대상 함수/시그니처 (제안 — 테스트가 이 계약에 매핑)

```python
# cookie.py
def format_cookie_header(cookies: list[tuple[str, str, str]]) -> str: ...
#   naver 필터(_select_naver 재사용) + "; ".join. naver 없으면 "".
#   parse_cookie_file은 내부적으로 이 함수를 쓰되, 빈 결과일 때 기존처럼 InvalidCookieFile을 던진다.

# cookie_login.py
HELPER_FLAG = "--__cookie-login"
def normalize_cookies(raw: object) -> list[tuple[str, str, str]]: ...   # SimpleCookie 리스트 → 삼중쌍
def parse_helper_output(returncode: int, stdout: str) -> str | None: ... # 헤더 or None
def login_and_capture(...) -> str | None: ...   # 헬퍼 서브프로세스 실행 + parse_helper_output (subprocess 주입 가능하게)
def run_helper(result_path: str | None = None) -> int: ...   # 헬퍼 측 pywebview 플로우(E2E)

# gui.py
#   - self.cookie_login_btn = ft.Button("네이버 로그인", on_click=self._cookie_login)
#   - def _cookie_login(self, e): self.page.run_thread(self._run_cookie_login)
#   - def _run_cookie_login(self): header = login_and_capture(); header → save_cookie + 성공 상태 / None → 실패 상태
#   - gui.main(): HELPER_FLAG in sys.argv면 run_helper()로 분기, 아니면 ft.run(_view)
```

## 7. 코드 포인터

- 쿠키 파싱/저장/로드: `src/naver_post_crawler/cookie.py`
  - `parse_cookie_file`(L32), `_select_naver`(L117), `_is_naver_domain`(L107),
    `save_cookie`(L155), `load_cookie`(L168), `InvalidCookieFile`(errors.py).
- 소비 경로(변경 금지): `gui.py` `_build_source`(L549) → `cookie = str(options["cookie"]) or load_cookie()`(L566).
- 기존 파일 버튼 흐름(미러 대상): `gui.py` `cookie_update_btn`(L169), `_pick_cookie_file`(L272),
  `_update_cookie`(L282), `_refresh_cookie_status`(L302), `_set_cookie_status`(L309).
- 오프스레드 패턴: `gui.py` `_start`(L458) → `self.page.run_thread(self._crawl)`(L466).
- 진입점: `gui.main`(L740)=`ft.run(_view)`; flet build/pack 셔임 `src/main.py`.
- 고급 옵션 컨테이너에 버튼 추가 위치: `gui.py` L169~L190 근처(쿠키 Row).
- 테스트 관례: `tests/test_gui.py`(fake 컨트롤 + `object.__new__`), `tests/test_cookie.py`,
  `tests/conftest.py`(scripts import 경로). 실행: `uv run pytest -q`. 린트: `ruff check`.

## 8. 의존성

- `pywebview>=6.0`(현재 6.2.1) 추가. Windows 백엔드용 `pythonnet`(WebView2)은
  `sys_platform == 'win32'` 마커로 추가. (스파이크에선 `uv pip install`로 venv에만
  넣었으니, 구현 시 `pyproject.toml`에 정식 반영하고 `uv sync`로 재현.)
- WebView2 런타임은 Win11 기본 탑재(추가 설치 불필요).

## 9. 완료(GREEN)의 정의

- Test-1~8을 1:1 인코딩한 테스트가 모두 통과(뮤테이션으로 단언 강도 확인).
- 기존 스위트 회귀 없음(`uv run pytest -q`).
- E2E-1은 R3에서 실환경 1회 실행으로 확인(스파이크가 쿠키 추출까지 이미 실증).
