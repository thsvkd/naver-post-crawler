# 핸드오프 — 배포·자동업데이트 이관 (flet build + Velopack)

> R1 합의 결과다. 이 문서가 이후 새 컨텍스트 에이전트들의 유일한 입력이다.
> 탐색 부산물은 버렸고, 합의된 것만 남겼다.

## 0. 확정 전제 (변경 금지)

1. 빌드 경로는 `flet build` 로 간다. 현행 `flet pack` 경로는 제거한다.
2. 설치/자동업데이트는 Velopack 으로 통일한다. 커스텀 `updater.py` 는 폐기한다.
3. Windows 와 macOS 를 모두 정식 지원한다.
4. 코드 서명·공증은 하지 않는다. 다만 환경변수로 서명 인자를 주입할 수 있는 구조는 남긴다.

## 1. 차단 요소 (착수 전 선결)

| # | 문제 | 근거 | 대응 |
|---|---|---|---|
| B-1 | `flet build` 임베드 인터프리터는 CPython 3.12인데 소스가 3.14 전용 문법 사용 → 배포본이 import 단계에서 즉사 | 3.12.11로 `src/` 전체 compile 시 `cookie_login.py:69` SyntaxError (실측) | W-1 |
| B-2 | flet 러너는 argv가 있으면 파이썬을 실행하지 않음 → 쿠키 로그인 헬퍼와 Velopack 설치 훅이 모두 무력화 | 템플릿 `lib/main.dart` 의 개발자 모드 분기 | W-4, W-8 |
| B-3 | 출력·로그가 cwd 상대 → 설치본에서 `current\` 에 쌓였다가 업데이트 때 소실 | `gui.py:583,589`, `cli.py:78-83,130-136` | W-5 |

## 2. 인간 결정 게이트 결과

| ID | 결정 | 출처 |
|---|---|---|
| D-1 | requires-python을 >=3.12로 낮추고 .python-version/ruff/uv.lock을 함께 맞춘다 | 직접 결정 |
| D-2 | .pkg 설치기만 배포하고 '나만 사용(~/Applications)'을 권장 | 권고안 채택 |
| D-3 | 미서명 Apple Silicon 실행 가능 여부를 Test-41로 먼저 실측한 뒤, 실패 시 --signAppIdentity "-" 로 ad-hoc 재서명 | 권고안 채택 |
| D-4 | packId='NaverPostCrawler', 데이터 폴더는 'naver-post-crawler' 유지, bundle_id='com.thsvkd.naverpostcrawler' | 권고안 채택 |
| D-5 | pyobjc 계열을 sys_platform=='darwin' 마커로 추가하고 번들 반입을 스파이크로 검증. 실패 시 쿠키 수동 지정으로 하강 | 직접 결정 |
| D-6 | tests/test_updater.py 폐기 승인 + velopack_update 래퍼용 축소 테스트로 대체 | 직접 결정 |
| D-7 | --check-update 유지하되 is_installed()로 3-way 분기(설치본/비설치본/오류) | 권고안 채택 |
| D-8 | sync_version() 이식 — __init__.py를 생성물로 전환. test_version.py는 생성물 최신성 검증으로 의미 전환 | 권고안 채택 |
| D-9 | vpk upload github --merge --tag v{version} 로 업로드(gh 직접 구현 대신) | 권고안 채택 |
| D-10 | 릴리스 노트 자동 생성(claude -p) 미채택 — 사람이 작성해 파일로 넘긴다 | 권고안 채택 |
| D-11 | 전환 안내용 포터블 릴리스(v0.1.1)를 한 번 더 내고, 다음 릴리스부터 Velopack | 권고안 채택 |
| D-12 | GUI는 사용자 폴더 기본값 + 마지막 선택 영속화, CLI는 cwd 상대 유지. 로그는 양쪽 모두 앱 데이터 아래 | 직접 결정 |
| D-13 | macOS는 arm64 우선(lipo -archs 실측 후 universal이면 그대로), Linux는 명시적 제외 + 코드의 linux 분기 제거 | 권고안 채택 |
| D-14 | 전환 안내 v0.1.1 → 첫 Velopack 릴리스 v0.2.0 | 권고안 채택 |

## 3. 기각한 대안

- **flet pack 유지 + Velopack만 도입** — flet pack 산출물에는 Flutter 러너가 없어 Velopack Windows 설치 훅(--veloapp-*)을 처리할 진입점이 없다. 참조 프로젝트와 구조가 갈라져 유지보수 대상이 두 벌이 된다.
- **Python 3.14 유지 + 회귀 테스트로만 강제** — 문법 오류는 잡지만 3.12/3.14 런타임 API 차이는 못 잡는다. '개발에서는 되는데 배포본에서 죽는' 클래스를 구조적으로 없애려면 개발 환경을 배포 인터프리터에 맞춰야 한다.
- **플랫폼별로 릴리스 태그 분리** — GithubSource는 /releases/latest가 아니라 최근 10개 릴리스를 스캔해 채널별 피드를 합친다. 태그를 분리하면 이 창을 두 배로 소진한다. 채널 기본값이 OS 이름이라 파일명도 충돌하지 않으므로 같은 태그 통합이 정답이다.
- **gh release create/upload 직접 구현** — draft 생성·재사용, 같은 채널 중복 거부, 릴리스 노트 비덮어쓰기가 vpk upload github --merge 에 이미 구현돼 있다.
- **릴리스 노트를 claude -p 로 자동 생성** — 2플랫폼 배포에서 두 머신이 서로 다른 노트를 만드는 실패 모드가 있고, 배포 사전 준비에 claude CLI 로그인이 추가된다.
- **Velopack 릴리스에 옛 에셋 이름으로 Setup.exe 를 담아 구 updater가 교체하게 하기** — 동작은 하지만 앱 자리에 설치 마법사가 놓이고 백업 파일이 남아 사용자가 혼란을 겪는다.
- **GitHub Actions 로 2플랫폼 자동 빌드** — macOS 러너 과금 배율 10배. 수동 2단계 배포를 먼저 실측 검증한 뒤 그 스크립트를 감싸는 형태로 나중에 얹는다.
- **기존 포터블 사용자 데이터 자동 마이그레이션** — 설치본은 옛 <exe폴더>/storage/ 위치를 알 방법이 없다. 쿠키는 만료되는 세션이라 '설치 후 1회 재로그인' 안내로 갈음한다.

## 4. 작업 항목 (의존 순서)

### W-1 — Python 3.12 호환성 선결 + 회귀 테스트

- 선행: 없음
- 파일: `src/naver_post_crawler/cookie_login.py`, `pyproject.toml`, `.python-version`, `uv.lock`, `tests/test_python_compat.py`

flet build가 임베드하는 인터프리터는 CPython 3.12다(참조 산출물 tarball 실측). uv의 3.12.11로 src/ 전체를 compile한 결과 cookie_login.py:69 `except json.JSONDecodeError, TypeError, ValueError:`가 SyntaxError('multiple exception types must be parenthesized')다. 같은 파일 :130 `except ValueError, IndexError:`도 동일 형태(compile은 파일당 첫 SyntaxError만 보고하므로 69행 수정 후 드러난다).

작업: (1) 두 줄을 괄호 형태로 수정. (2) pyproject.toml:7 requires-python을 '>=3.12'로 낮추고 .python-version, [tool.ruff] target-version='py312', uv.lock을 함께 맞춘다. (3) '3.12로 src/ 전체 compile' 회귀 테스트를 추가한다 — uv가 관리하는 cpython-3.12.11이 이 머신에 이미 있으므로 자동화 가능하다(인터프리터 미존재 시 skip이 아니라 명시적 실패로 두어 조용한 통과를 막는다).

이 항목이 전체 이관의 최선행이다. 3.14 문법이 하나라도 남아 있으면 이후 모든 빌드 산출물이 import 단계에서 죽는데, 개발 머신에서는 절대 재현되지 않는다.

### W-2 — 릴리스 저장소 URL 정정(naver-blog-crawler → naver-post-crawler)

- 선행: 없음
- 파일: `src/naver_post_crawler/updater.py`, `README.md`

git remote -v 실측 결과 origin은 https://github.com/thsvkd/naver-post-crawler.git 인데, updater.py:39-40이 REPO_NAME='naver-blog-crawler'(옛 이름)를 하드코딩하고 있다. GitHub API 호출이 301 리다이렉트로 우연히 동작 중이며(curl 실측: 301 → /repositories/1249328234/...), Velopack GithubSource의 다운로더가 리다이렉트를 따라간다는 보장이 소스상 없다. 저장소 이름이 재사용되면 잘못된 저장소를 보게 된다.

작업: updater.py:39-40과 README.md:7, README.md:36의 링크를 정식 이름으로 정정한다. updater.py는 W-11에서 삭제될 예정이지만, 이 정정을 먼저 독립 커밋으로 두어야 '삭제 전까지 잘못된 URL로 동작' 구간이 없다.

### W-3 — GUI 창 크기 SSOT 상수 승격(동작 불변 리팩터)

- 선행: 없음
- 파일: `src/naver_post_crawler/gui.py`

참조 scripts/flet_template.py는 gui.py 소스를 정규식 `^_WINDOW_(WIDTH|HEIGHT)\s*=\s*(\d+)\b`(MULTILINE)로 직접 파싱해 네이티브 러너의 초기 창 크기를 패치한다(파이썬 모듈을 import하면 flet까지 딸려 오므로 소스 파싱). 이 저장소는 gui.py:97-98이 `page.window.width = 760` / `height = 720`으로 _build() 안 인라인이라 매칭 실패로 즉시 fail()한다.

작업: gui.py 모듈 최상단에 `_WINDOW_WIDTH = 760` / `_WINDOW_HEIGHT = 720`을 신설하고(이름/형식을 바꾸면 스크립트 정규식도 함께 고쳐야 한다는 SSOT 주석 포함) 97-98줄이 그 상수를 참조하게 한다. min_width/min_height(99-100)는 러너 패치 대상이 아니므로 상수화는 선택.

R0상 refactor이므로 새 RED을 만들지 않는다. 기존 tests/test_gui.py가 계속 green인지로 검증하고, W-6의 flet_template 테스트가 이 상수를 SSOT로 참조하게 된다.

### W-4 — 쿠키 로그인 헬퍼 기동 방식을 argv → 환경변수로 재설계

- 선행: W-1
- 파일: `src/naver_post_crawler/cookie_login.py`, `src/naver_post_crawler/gui.py`, `src/naver_post_crawler/__main__.py`, `tests/test_cookie_login.py`

flet 빌드 템플릿의 lib/main.dart가 `_args.isNotEmpty && isDesktopPlatform()`이면 개발자 모드로 판정해 runPythonApp()을 호출하지 않는다. 따라서 cookie_login.py:84-90의 `_helper_command()`가 앱 exe를 HELPER_FLAG와 함께 재실행하는 방식은 flet build 배포본에서 성립하지 않는다(로그인 창 대신 빈 FletApp). 게다가 분기 조건 `getattr(sys, 'frozen', False)`는 PyInstaller 전용 플래그라 flet build에서는 False로 떨어져 `[sys.executable, '-m', 'naver_post_crawler', ...]`라는 잘못된 커맨드를 만든다.

작업: 헬퍼 모드 전달을 argv가 아니라 환경변수(예: NAVER_POST_CRAWLER_HELPER=cookie-login, 결과 경로도 환경변수)로 바꾸고, gui.py:786-792의 main() 분기와 __main__.py 진입 판정을 그에 맞춘다. frozen 판정도 flet build 산출물에서 유효한 신호(FLET_APP_STORAGE_DATA 존재 등)로 교체한다. 기존 HELPER_FLAG 경로는 개발 실행 호환을 위해 남길지 함께 결정한다.

실제 로그인 창이 뜨는지는 자동 테스트로 못 잡으므로 W-16의 E2E 항목(Test-43)으로 넘긴다. 단위 테스트로는 '헬퍼 커맨드가 argv에 HELPER_FLAG를 넣지 않는다'를 잠근다.

### W-5 — 앱 데이터·출력 폴더·로그 경로 정책 정리

- 선행: W-1
- 파일: `src/naver_post_crawler/cookie.py`, `src/naver_post_crawler/gui.py`, `src/naver_post_crawler/cli.py`, `tests/test_cookie.py`, `tests/test_gui.py`

Velopack은 Windows에서 %LocalAppData%\<PackId>\current\를, macOS에서 .app 번들을 통째로 교체한다. 현재 정책은 세 곳이 이 모델과 충돌한다.

(1) cookie.py:142-165 app_data_dir()의 `sys.frozen` 분기(실행 파일 옆 storage/)와 'updater가 exe를 교체해도 유지된다'는 docstring을 제거한다. FLET_APP_STORAGE_DATA 우선 분기는 유지(flet 런타임과 `flet run` 모두 이 변수를 세운다), 폴백은 현행 플랫폼별 사용자 경로를 그대로 쓴다. 'FLET_APP_STORAGE_DATA가 없어도 번들 내부를 절대 쓰지 않는다'를 회귀 테스트로 고정한다.

(2) 로그: gui.py:589의 `'log_dir': Path('logs')` 하드코딩(사용자가 바꿀 수도 없음)을 app_data_dir()/'logs'로 옮기고 상수화한다(CLAUDE.md 하드코딩 금지).

(3) 출력 폴더: gui.py:117-119 기본값 'output'과 gui.py:583의 cwd 상대 해석이 설치본에서 current\output에 쌓였다가 다음 업데이트에서 소실되는 경로다. 참조 gui.py:175-215처럼 마지막 선택 폴더를 app data 아래 설정 파일에 영속화하고, 미설정 시 사용자 폴더(바탕화면/문서, OneDrive 리다이렉션 고려)를 기본값으로 계산한다. CLI의 -o/--log-dir 기본값을 함께 바꿀지는 인간 결정 사항(D-12).

.failures.json은 출력물과 짝이므로 out_dir 안에 두는 현행 유지(폴더째 옮겨도 따라감).

### W-6 — pyproject 의존성 재정비(flet 버전 핀 + velopack 추가 + pyinstaller 제거)

- 선행: W-1
- 파일: `pyproject.toml`, `uv.lock`

(1) flet-desktop(pyproject.toml:13)과 dev 그룹의 flet-cli(:38)가 둘 다 '>=0.85.1' 하한만 걸려 있다. flet build는 이 제약으로 번들용 flet을 별도 설치하므로 >= 로 두면 번들 flet과 flet-cli가 만든 UI 클라이언트 버전이 어긋나 '앱은 뜨지만 화면이 안 그려지는 빈 창'이 된다. flet pack에서는 런타임 flet과 UI 클라이언트가 같은 프로세스라 드러나지 않던 실패 모드다. 양쪽을 '==0.85.1'로 못박고(현재 lock이 0.85.1) 버전 상향 시 함께 올린다는 주석을 남긴다.

(2) [project].dependencies에 velopack>=1.2.0 추가. optional이나 dev로 두면 flet build 번들에 들어가지 않는다(flet build는 [project].dependencies와 [tool.flet.*.dependencies]만 번들한다). PyPI 실측 결과 1.2.0은 win32/win_amd64/win_arm64 + macosx_10_12_x86_64/macosx_11_0_arm64 abi3 휠을 모두 제공한다.

(3) dev 그룹의 pyinstaller>=6.0 제거(flet pack 전용). 같은 줄의 주석('flet pack(scripts/build.py의 유일한 빌드 경로)')도 함께 고친다. dev 그룹에 flet-cli를 두는 이유(uv sync churn 방지)는 유지한다.

(4) [tool.flet.app]의 module='main' / path='src'는 이미 flet build용으로 유효하므로 그대로 둔다. boot_screen/startup_screen 추가(flet build는 flet pack 대비 첫 실행 압축 해제 대기가 길다)와 org/bundle_id는 D-4 결정 후 반영.

### W-7 — scripts/_common.py 보강(UTF-8 콘솔 + pyproject_version)

- 선행: 없음
- 파일: `scripts/_common.py`

대상 _common.py(58줄)와 참조(101줄)는 info/fail/require_uv/run/check가 거의 동일하다. 차이는 셋뿐이다.

(1) UTF-8 콘솔 강제 블록(참조 :23-25)이 없다. 현재 build.py는 자식 프로세스에만 PYTHONUTF8을 넘기고(scripts/build.py:153) info()/fail() 자신의 출력은 보호되지 않아, 한국어 Windows(cp949)에서 안내 문구가 UnicodeEncodeError를 낼 수 있다. sys.stdout.reconfigure 블록을 그대로 옮긴다.

(2) pyproject_version()(참조 :74-80)이 없다. tomllib import를 함께 추가한다. build.py가 vpk --packVersion에, deploy.py가 버전 게이트에 쓴다.

(3) sync_version()(참조 :83-101) 이식 여부는 D-8 결정 사항이다. 이식한다면 _INIT_PATH를 REPO_ROOT/'src'/'naver_post_crawler'/'__init__.py'로 바꾸기만 하면 참조의 정규식이 이 저장소의 `__version__ = "0.1.0"` 형식과 그대로 일치한다.

REPO_ROOT 계산과 run/check 시그니처는 동일하므로 손대지 않는다.

### W-8 — scripts/flet_template.py 이식 + tests/test_flet_template.py

- 선행: W-3, W-7
- 파일: `scripts/flet_template.py`, `tests/test_flet_template.py`

flet build 경로에서만 의미가 있는 Windows 러너 패치다(flet pack에는 Flutter 러너 자체가 없다). 두 가지를 고친다.
(a) Velopack 훅 조기 종료: main.cpp에서 `::wcsstr(command_line, L"--veloapp-")`이면 즉시 성공 종료. 파이썬 쪽 대안이 없다(§PRD 3-2).
(b) 첫 창 크기 1280x720 → 760x720(깜빡임 제거).

이식 시 바꿀 것: 창 크기 값(760,720), _GUI_PATH를 src/naver_post_crawler/gui.py로, 캐시 스탬프 파일명('.yke-patch' → 프로젝트명 기반), 주석의 [yke] 태그. _PATCH_REVISION과 cache_dir_name()의 설계 의도(flet이 템플릿 '경로'만 해시하므로 패치 내용을 경로 이름에 인코딩해야 재생성됨)는 반드시 유지한다 — 이걸 빠뜨리면 '패치했는데 반영 안 되는' 유령 버그가 난다.

테스트: 참조 tests/test_flet_template.py의 8개를 760x720으로 바꿔 이식하고(pytest가 unittest.TestCase를 그대로 수집하므로 변환 불필요), 참조에 없는 4개를 보강한다 — 훅 인자 4종 각각 매칭, 조기 return이 ::CoInitializeEx보다 앞(COM 짝 불일치 방지), gui.py 우변이 리터럴이 아니라 상수인지, 캐시 스탬프 키와 cache_dir_name의 동기. sys.path 조작은 기존 tests/conftest.py:15가 이미 scripts/를 append하므로 그대로 활용한다.

macOS에는 이 패치를 적용하지 않는다(macos 러너는 dartEntrypointArguments를 설정하지 않아 args가 비고, 초기 창 크기는 MainMenu.xib의 800x600이라 C++ 한 줄이 아니라 XML 패치가 필요해 유지보수 비용이 뚜렷하게 높다).

### W-9 — scripts/sign.py 신설(미설정 시 서명 스킵, Windows/macOS 인자 체계 분리)

- 선행: W-7
- 파일: `scripts/sign.py`, `tests/test_sign.py`

확정 결정 4(미서명 배포 + 서명 인자를 환경변수로 끼울 수 있는 구조)에 정확히 부합하는 파일이다. 참조에서 세 계약을 옮긴다: _sign_args()(인증서 미지정이면 None → 스킵), velopack_sign_params()(vpk pack --signParams 문자열, 비밀번호 /p를 로그에 남기지 않는 마스킹 포함), maybe_sign_bundle()(미지정 시 '건너뜀' 로그만 남기고 False).

환경변수 접두사를 프로젝트에 맞게 바꾸고(YKE_SIGN_* → NPC_SIGN_*), _APP_EXE를 이 앱 실행 파일 이름으로 교체한다.

중요: macOS는 --signParams를 쓰지 않는다. vpk osx pack의 서명 인자는 --signAppIdentity / --signInstallIdentity / --signEntitlements / --signDisableDeep / --notaryProfile / --keychain으로 완전히 다른 체계다. 따라서 velopack_sign_params_win()과 velopack_sign_args_macos() -> list[str] 두 함수로 나누고, 각각 대응 환경변수가 없으면 None/빈 리스트를 돌려 미서명으로 진행하게 한다. find_signtool()은 Windows 전용이므로 호출을 타깃 분기 안에 둔다.

vpk 자체는 --signAppIdentity가 비면 경고만 찍고 통과하므로(OsxPackCommandRunner.CodeSign) 미서명 빌드가 실패하지 않는다.

### W-10 — scripts/build.py 전면 재작성(flet build + vpk pack, win/mac 분기) + 순수 함수 단위 테스트

- 선행: W-6, W-7, W-8, W-9
- 파일: `scripts/build.py`, `tests/test_build_script.py`

현행 build.py(164줄)는 flet pack 전용이라 재사용 가능한 부분이 사실상 없다.

삭제: _PACK_ENTRY/_PACK_NAME/_PACK_DIST/_PACK_WORK(:37-42), _pack_artifact_path(:45-52), _clean_prior_artifact(:55-76), verify_pack_artifact(:79-86), pack_app(:89-111), compress_pack_artifact(:121-144). 특히 compress_pack_artifact는 커스텀 updater의 에셋 계약(naver-post-crawler-<target>.zip)을 만드는 함수라 Velopack 전환 시 존재 이유가 사라진다.
유지: main()의 PYTHONUTF8/PYTHONIOENCODING 강제(:150-153), _current_pack_target 매핑(:114-118), _PRODUCT='Naver Blog Backup'(:32).

참조에서 이식: _target(), ensure_windows_toolchain()(flet build는 flet pack과 달리 Flutter/MSVC 네이티브 툴체인이 필요하다 — build.py:13의 '툴체인 불필요' 주석은 무효가 된다), flet_version()(템플릿 태그와 정확히 같은 값을 얻기 위한 서브프로세스 조회), stash_output()/verify_artifact(), _find_vpk()(shutil.which → ~/.dotnet/tools/vpk 폴백, os.name 분기가 이미 있어 macOS에서 그대로 동작), write_windows_launcher()/_LAUNCHER_PS1/_LAUNCHER_BAT, velopack_pack().
삭제할 참조 코드: build_gpu_runtime_asset(), _GPU_DEPS/_GPU_RUNTIME_TAG/_GPU_RUNTIME_ASSET, --gpu-runtime 인자(이 앱에 GPU 개념이 없다).

macOS 분기는 신규 설계다: --packDir에 flet build macos 산출물인 .app 경로를 그대로 넘기고(.app으로 끝나면 Velopack이 그대로 복사해 쓴다), --icon/--plist는 생략, --channel은 기본값 osx, --mainExe는 Info.plist가 진입점을 정하므로 필수가 아니다(실측 확인 대상). verify_artifact도 macOS는 '비어 있지 않은지'가 아니라 *.app 존재 검증으로 강화한다. Windows 타깃에만 --template(W-8)을 붙인다.

델타를 만들려면 pack 이전에 `vpk download github --channel <ch>`로 이전 full nupkg를 받아 둬야 한다. download가 이전 버전 nupkg를 같은 폴더에 떨어뜨리므로 이후 업로드에서 버전으로 좁혀 잡아야 한다.

테스트 가능성 설계: 참조 build.py:315-320은 커맨드 조립이 main() 안에 인라인이라 테스트 불가능하다. 이 저장소에서는 build_cmd 조립과 vpk pack 인자 조립을 순수 함수로 분리해 macOS 개발 머신에서 Test-9~12를 자동으로 잠근다.

### W-11 — src/naver_post_crawler/velopack_update.py 신설 + 지연 임포트 회귀 테스트

- 선행: W-2, W-6
- 파일: `src/naver_post_crawler/velopack_update.py`, `tests/test_velopack_update.py`

참조 src/yke/velopack_update.py(136줄)를 그대로 옮긴다: run_startup_maintenance(velopack App().run() — 오래된 nupkg 정리 + 받아둔 업데이트 적용 후 재시작), _manager()(모듈 전역 캐시, velopack import를 이 함수 안에서만), is_installed(), current_version(), check(), target_version(info), download(info, progress_cb)(velopack은 0~100 int를 주므로 0.0~1.0으로 환산 — 기존 gui.py:471-472의 progress_cb 시그니처와 동일해 그대로 재사용 가능), apply_and_restart(info).

REPO_URL은 정정된 저장소(https://github.com/thsvkd/naver-post-crawler)를 쓴다. 참조는 build.py와 velopack_update.py 두 곳에 각각 문자열로 두고 주석으로만 '반드시 일치'를 강제하는데, 이 저장소는 하드코딩 금지 규칙에 맞춰 이 모듈을 단일 출처로 삼고 build.py가 그 값을 읽게 하거나 최소한 일치를 테스트로 강제한다.

macOS 때문에 참조에 없는 것을 추가한다: is_portable()(velopack 1.2.0 스텁의 UpdateManager.get_is_portable()). 또한 macOS는 .app 번들 안에서 실행돼야만 is_installed()가 True다(네이티브 모듈 문자열 "Could not locate '.app' in executable path", "UpdateMac does not exist in the expected path") — Windows 기준 문구("설치본에서만 동작")만 쓰면 macOS 사용자에게 오해를 주므로 플랫폼별 안내 문구 분기가 필요하다.

UpdateOptions.ExplicitChannel은 None으로 둔다(스텁 docstring: 'This option should usually be left None' — 채널은 설치된 패키지 메타데이터에 박혀 있어 macOS 설치본이 자동으로 releases.osx.json을 본다).

회귀 테스트: 'velopack_update를 import해도 sys.modules에 velopack이 들어오지 않는다'(성능 회귀는 테스트로 잘 안 잡히므로 명시적으로 잠근다), 진행률 환산, is_installed/run_startup_maintenance의 예외 삼킴.

### W-12 — GUI 업데이트 계층 교체(updater → velopack_update) + Button.content 버그 수정

- 선행: W-11
- 파일: `src/naver_post_crawler/gui.py`, `tests/test_gui.py`

gui.py의 업데이트 블록은 :359-472에 몰려 있고 참조와 구조가 이미 거의 동일하다.

그대로 재사용: UI 컨트롤 2개(:132-137), 레이아웃(:222-224), 기동 훅(:88-89 page.run_thread(self._auto_check_updates)), _set_update_status(:360-364), _on_update_click(:370-383), _applying 재진입 가드 + _reset_applying(:80-81, 376-382, 407-412) — 이 가드는 참조에 없고 이쪽이 더 낫다(참조는 재클릭 시 다운로드 스레드가 중복 기동된다).

본문 교체: import(:23)를 velopack_update로. _pending_release: updater.Release(:79) → _pending_update(velopack UpdateInfo). _check_updates(:385-405)는 앞머리에 is_installed() 가드를 넣고 check()로 교체. _download_and_apply(:414-469)에서 다음을 전부 삭제 — updater.is_packaged() 가드(:421-427, sys.frozen은 flet build에서 안 잡히므로 그대로 두면 설치본에서도 '개발 환경입니다'라며 업데이트를 영구 거부하는 조용한 기능 사망), **win32 전용 차단 분기(:428-437) — Velopack이 두 OS를 다 처리하므로 제거가 이번 작업의 핵심**, sha256 경고(:438-439, velopack이 nupkg 해시를 검증한다), tempfile.mkdtemp/install_exe/staging_dir/download/extract/unlink(:440-448). 대신 download(info, progress_cb) → 상태문구 → page.update() → time.sleep(0.4) → apply_and_restart(info).

반드시 함께 고칠 실제 버그: :397 `self.update_btn.text = ...`. 설치된 flet 0.85.1에서 dataclasses.fields로 실측한 결과 ft.Button에 text 필드가 없다(has text: False | has content: True). 즉 현재는 새 버전을 찾아도 버튼 라벨이 바뀌지 않는다. `.content`로 교체한다.

추가: _auto_check_updates(:366-368)의 첫 줄에 run_startup_maintenance()를 넣는다. velopack에서 packages 폴더의 낡은 nupkg를 지우는 유일한 지점이고, 이 앱의 전체 패키지는 64MB급이라 빠뜨리면 업데이트마다 쌓인다. velopack import가 0.5초+라 반드시 이 워커 스레드에서만 부른다.

컨트롤 갱신은 apply_and_restart 직전/직후 창이 해체되는 구간이 있으므로 참조의 _safe_update(try/except + logger.debug) 패턴 도입을 검토한다. updater 제거 후 tempfile(:14)이 미사용이 되는지 확인한다.

### W-13 — CLI --check-update 처리 + updater.py/test_updater.py 제거

- 선행: W-12
- 파일: `src/naver_post_crawler/cli.py`, `src/naver_post_crawler/updater.py`, `tests/test_updater.py`, `tests/test_cli.py`

cli.py:30이 updater를 import하고 :74에 플래그, :167-186에서 check_latest/current_target을 호출해 asset_url을 출력한다. Velopack에는 asset_url 개념이 없으므로 출력 문구를 다시 설계해야 한다.

현실적 제약: flet build 번들에는 click CLI 진입점이 들어가지 않는다([tool.flet.app] module='main', path='src'이고 __main__.py는 gui.main()만 부른다). 따라서 설치본에서 --check-update가 실행될 경로가 사실상 없고 개발 실행/uv run 전용으로 남는다. 유지/제거는 D-7 결정 사항이며, 유지 시 is_installed()로 3-way 분기(설치본 → check(), 개발 실행/포터블 → 릴리스 페이지 안내 후 종료 0, 예외 → 트레이스백 없이 경고)한다. 적용(apply)은 CLI에 두지 않는다 — apply_updates_and_restart는 GUI 앱을 재시작시키는 동작이라 CLI 프로세스에서는 의미가 없다.

cli.py:173-174의 em-dash(U+2014) 금지 주석(cp949 콘솔 UnicodeEncodeError)은 새 문구에도 그대로 적용한다.

제거: updater.py 415줄 전부(부분 존치할 로직 없음 — 버전 비교/에셋 선택/SHA256/사이드카를 Velopack이 전부 대체하고, current_target()의 타깃 매핑은 이미 build.py에 중복 존재하므로 빌드 스크립트 쪽 한 곳만 남긴다). tests/test_updater.py 490줄(테스트 28개)도 함께. 삭제 전 `grep -rn 'updater' src tests scripts`로 호출부를 전수 확인하고, 삭제 후 `python -c 'import naver_post_crawler.cli, naver_post_crawler.gui'`로 임포트 그래프를 검증한다. 테스트와 구현은 반드시 같은 커밋으로 제거한다.

주의: tests/test_updater.py는 인간과 합의된 Test-1~15,17~23이므로 D-6 폐기 승인 없이는 삭제할 수 없다.

### W-14 — scripts/deploy.py 신설(2플랫폼 릴리스, 태그 통합 + 노트 1회 생성 게이트)

- 선행: W-10, W-11
- 파일: `scripts/deploy.py`, `scripts/release_notes_guide.md`, `tests/test_deploy_script.py`

이 저장소에는 deploy 스크립트가 없다(scripts/에 _common/build/run/setup/test뿐).

참조에서 이식: pyproject_version() 기반 버전 게이트(이전 릴리스 태그와 같으면 fail — Velopack은 같은 버전 재배포가 업데이트 피드를 망가뜨린다), _latest_release_tag()(gh release list --json으로 ^v\d+\.\d+\.\d+$ 태그만), _commit_log_since() + generate_release_notes()(claude -p, scripts/release_notes_guide.md도 함께 이식해야 동작한다 — 채택 여부는 D-10).

2플랫폼 대응으로 새로 설계할 것:
(1) 태그 통합. Velopack 채널이 OS 이름이라 win/osx 산출물의 파일명이 하나도 겹치지 않으므로 같은 태그 v<version> 한 릴리스에 올린다. 플랫폼별 태그 분리는 GithubSource의 per_page=10 스캔 창을 두 배로 소진한다.
(2) 순서 규약. 첫 플랫폼은 draft로 남기고(--publish 생략), 두 번째 플랫폼이 --merge --publish로 합류시킨다. 이러면 '한쪽만 올라간 중간 상태'가 draft라서 사용자와 비인증 GithubSource 양쪽에 노출되지 않는다.
(3) 업로드 도구는 D-9 결정 사항이다. gh를 유지하면 'create 실패 시 upload 폴백' 분기를 직접 구현해야 하고(참조는 create만 호출하므로 두 번째 플랫폼이 그대로 깨진다), vpk upload github --merge를 쓰면 draft/merge/중복 방지가 이미 구현돼 있는 대신 해당 채널의 모든 산출물이 전부 올라간다(pack 단계 --noPortable로 대응).
(4) 에셋 글롭을 채널에서 파생시키고 반드시 이번 버전으로 좁힌다 — vpk download가 델타 기준으로 이전 버전 nupkg를 같은 폴더에 내려받으므로 *.nupkg를 전부 주우면 이전 버전까지 올라간다.
(5) 릴리스 노트는 첫 플랫폼에서만 생성한다. 두 머신에서 각각 claude -p를 돌리면 내용이 달라진다. 단, _latest_release_tag()가 draft를 제외하므로 '이번 태그의 draft가 이미 있는지'는 draft를 포함해 조회하는 별도 함수로 판정해야 한다.
(6) 기본 태그가 `latest.Version.ToString()`(= 0.2.0, v 없음)이므로 기존 관행(v0.1.0)을 지키려면 --tag v{version}을 반드시 명시한다.

실패한 배포로 생긴 draft 릴리스는 즉시 삭제하는 절차를 스크립트나 체크리스트에 넣는다(GithubSource가 파싱하는 릴리스 모델에 draft 필드가 없어 필터링하지 않는다).

### W-15 — scripts/setup.py 보강 + 문서·.gitignore 갱신

- 선행: W-13, W-14
- 파일: `scripts/setup.py`, `.gitignore`, `README.md`, `docs/SPEC.md`

setup.py: 현행판(:38-45)은 uv sync + git pre-commit hook 설치라는 이 저장소 고유 기능이 있어 참조판으로 갈아끼우면 안 된다. 이식할 것은 (a) sync_version() 호출 한 줄(D-8에서 채택 시), (b) 빌드 선행 조건 점검 — vpk 존재(dotnet tool install -g vpk), Flutter/Visual Studio(Windows)/Xcode(macOS), macOS의 zstd(Velopack은 Unix에서 번들 zstd가 아니라 시스템 zstd를 요구한다)뿐이다. run.py(:22-32, 인자 없으면 GUI/있으면 CLI)와 test.py(:13-21, ruff 린트+포맷+pytest, pre-commit hook이 이 파일을 부른다)는 flet build 전환과 무관하므로 손대지 않는다.

.gitignore:19-24: 주석이 정확히 뒤집힌다. 지금은 '/dist/, /.pack-build/가 현역이고 /build/는 과거 잔재'인데, 이관 후 /build/(flet build 작업 디렉터리 + build/_flet_template 캐시)가 현역이 되고 /.pack-build/는 사라진다. 참조처럼 /build/와 /dist/만 남기고 주석을 현재 상태에 맞게 고친다.

README.md 12곳: 저장소 링크(:7, :36 — W-2에서 이미 정정), '설치 불필요, 압축 해제 후 즉시 실행'(:23) → 설치기 기반, 자동 업데이트 설명(:24, :231-237) → Velopack, 'Windows 실행 파일만 제공'(:30-32) → macOS 설치본 포함, 에셋 이름(:36), '게시된 릴리스가 아직 없다면' 주석(:38-40 — v0.1.0이 이미 있으므로 사실과 다름), 설치 절차(:42-45), SmartScreen 안내(:47-49, :169-176)는 존치하되 macOS Gatekeeper 안내 신설, 빌드 섹션 전체(:304-313, 'flet pack으로 단일 실행파일' / 'Flutter·네이티브 툴체인 불필요' / 결과물 경로) → flet build + vpk로 전면 교체 + 선행 조건 명시, '앱 데이터는 실행 파일 옆 storage/'(:313) → 새 경로 정책, --check-update 옵션(:300), 출력 폴더 기본값(:105).

docs/SPEC.md: :130 'Flet 기반 Windows 데스크톱 앱' → Windows/macOS, :136-138 '네이티브 빌드는 scripts/build.py' → flet build 타깃 + Velopack 패키징(현재 문장은 flet pack을 '네이티브 빌드'라 불러 이관 후 오해를 부른다), :125 로깅 위치, 그리고 **'10. 배포·자동 업데이트' 장 신설**(설치 위치, 채널, 릴리스 에셋 구성, 앱 데이터 경로 정책, 미서명 배포의 사용자 영향, Gatekeeper 우회 절차). SPEC에 배포 장이 아예 없는 이유는 updater.py의 계약이 코드 docstring(:1-16)에만 있었기 때문이며, 이관을 계기로 SSoT를 문서로 올린다.

### W-16 — 실기 E2E 릴리스 리허설(Windows + macOS)

- 선행: W-15
- 파일: `docs/SPEC.md`

자동 테스트로 잠글 수 없는 영역만 실제 실행으로 검증하고 증거 번들을 남긴다. 테스트 코드가 이미 검증한 동작은 재확인하지 않는다.

macOS(개발 머신): flet build macos → .app 산출 확인 → vpk pack(미서명) → .pkg 생성 여부 → 설치 → Gatekeeper 우회 절차 실측 → 실행 → 앱 내 네이버 로그인 1회 → 업데이트 확인/다운로드/적용/재시작 → 적용 후 쿠키·설정·백업 결과물 잔존 확인. Apple Silicon에서 ad-hoc 서명 없이 실행되는지가 가장 큰 미지수다(Velopack이 UpdateMac과 sq.version 심볼릭 링크를 추가해 기존 번들 서명을 무효화하는데, --signAppIdentity가 없으면 재서명을 하지 않는다). 그리고 macOS에서 --veloapp-* 훅이 argv로 실제 전달되는지 관측한다(전달되면 창이 뜨고 타임아웃되며, 그 경우 App().run()을 src/main.py 최상단 동기 호출로 올리는 배치를 설계해야 한다).

Windows(실기): flet build windows → 패치된 main.cpp가 MSVC로 컴파일되는지 → vpk pack → Setup.exe 설치 시 '설치가 부분적으로 성공했습니다' 경고 부재 → 첫 창 크기 깜빡임 부재 → 앱 내 네이버 로그인 → OTA 적용/재시작 → 데이터 잔존.

공통: 같은 태그에 win/osx 자산 병합 업로드 후 릴리스에 releases.win.json과 releases.osx.json이 모두 존재하는지, 두 번째 릴리스에서 델타 nupkg가 생성되는지.

확인된 계약(특히 macOS --mainExe 유무에 따른 동작)은 build.py 주석과 docs/SPEC.md에 근거와 함께 기록한다. 추측으로 코드를 고정하지 않는다.

## 5. 테스트 케이스 리스트 (완료의 정의)

각 테스트 코드에 `covers: Test-N` 태그를 단다. 이 리스트 밖의 케이스는 완료 범위 밖이다.

| ID | 종류 | 자동화 | 제목 | Given/When/Then |
|---|---|---|---|---|
| Test-1 | 단위 | 자동 | src/ 전체가 CPython 3.12에서 컴파일된다 | Given src/ 아래 모든 .py 파일이, When CPython 3.12 인터프리터로 compile()되면, Then SyntaxError가 하나도 발생하지 않는다. (현재는 cookie_login.py:69가 'multiple exception types must be parenthesized'로 실패하며 :130도 같은 형태다. 3.12 인터프리터를 못 찾으면 skip이 아니라 명시적 실패로 처리해 조용한 통과를 막는다.) |
| Test-2 | 단위 | 자동 | 창 크기가 모듈 상수 SSOT로만 정의된다 | Given gui.py 소스가, When _WINDOW_WIDTH/_WINDOW_HEIGHT 상수와 page.window.width/height 대입문을 검사하면, Then 상수가 각각 760/720으로 존재하고 대입문 우변이 리터럴이 아니라 그 상수를 참조한다. |
| Test-3 | 단위 | 자동 | 패치된 main.cpp가 Velopack 훅에서 창 생성 전에 조기 종료한다 | Given flet 템플릿의 windows/runner/main.cpp 발췌가, When patch_windows_runner()를 적용하면, Then 결과 문자열에 ::wcsstr(command_line, L"--veloapp-") 검사가 존재하고 그 위치가 FlutterWindow 생성보다 앞선다. |
| Test-4 | 단위 | 자동 | 조기 종료가 COM 초기화보다 앞선다(초기화/해제 짝 보존) | Given 패치된 main.cpp가, When 훅 조기 return 위치와 ::CoInitializeEx 호출 위치를 비교하면, Then return이 CoInitializeEx보다 앞선다(CoUninitialize 없이 종료하는 회귀 차단). |
| Test-5 | 단위 | 자동 | 훅 인자 4종이 모두 조기 종료 조건에 걸린다 | Given --veloapp-install / --veloapp-updated / --veloapp-obsolete / --veloapp-uninstall 각각이, When 패치의 매칭 조건에 넣으면, Then 네 가지 모두 조기 종료 경로로 판정된다. |
| Test-6 | 단위 | 자동 | 패치가 wchar.h를 선언하고 초기 창 크기를 GUI 상수와 일치시킨다 | Given 패치된 main.cpp가, When 내용을 검사하면, Then #include <wchar.h>가 추가돼 있고 Win32Window::Size size(760, 720);가 존재하며 원본의 1280, 720은 남아 있지 않다. |
| Test-7 | 단위 | 자동 | 앵커가 사라지면 조용히 넘어가지 않고 빌드를 실패시킨다 | Given 앵커 문자열이 제거되었거나 2회 이상 등장하는 main.cpp가, When patch_windows_runner()를 호출하면, Then ValueError가 발생한다. 이미 패치된 소스에 재적용해도 마찬가지로 ValueError가 발생한다(멱등 가드). |
| Test-8 | 단위 | 자동 | 템플릿 캐시 디렉터리 이름이 패치 내용 변화를 반영한다 | Given flet 버전/패치 리비전/창 크기 조합이, When cache_dir_name()을 호출하면, Then 세 값이 모두 이름에 인코딩되고 어느 하나만 바뀌어도 이름이 달라진다. 또한 prepare()가 쓰는 캐시 스탬프 키가 같은 입력에 함께 반응한다. |
| Test-9 | 단위 | 자동 | 빌드 타깃 매핑이 실행 OS에서 올바르게 파생된다 | Given platform.system()이 'Windows'/'Darwin'일 때, When 타깃 매핑 함수를 호출하면, Then 각각 'windows'/'macos'를 돌려준다. |
| Test-10 | 단위 | 자동 | flet build 커맨드 조립이 타깃별로 다르다 | Given 타깃이 windows일 때, When flet build 커맨드를 조립하면, Then --template 인자가 포함된다. 타깃이 macos이면 --template이 포함되지 않는다. |
| Test-11 | 단위 | 자동 | vpk pack 인자가 채널·packDir·mainExe 규칙을 따른다 | Given 타깃이 windows일 때, When vpk pack 인자를 조립하면, Then --channel win, --packDir이 산출물 폴더, --mainExe가 Windows 실행 파일명으로 들어간다. 타깃이 macos이면 --channel osx이고 --packDir이 .app으로 끝나는 경로다. 양쪽 모두 --packId와 --packVersion(pyproject 버전)이 포함된다. |
| Test-12 | 단위 | 자동 | 산출물 검증이 플랫폼별 계약을 확인한다 | Given macOS 빌드 산출 디렉터리에 .app이 없을 때, When verify_artifact()를 호출하면, Then 실패한다(폴더가 비어 있지 않다는 것만으로 통과하지 않는다). Windows는 실행 파일 부재 시 실패한다. |
| Test-13 | 단위 | 자동 | vpk download가 pack보다 먼저 호출되고 채널이 일치한다 | Given 빌드 파이프라인이, When 실행 순서와 인자를 검사하면, Then vpk download github이 vpk pack보다 먼저 호출되고(델타 생성 전제조건) 두 명령의 --channel 값이 같다. |
| Test-14 | 단위 | 자동 | 서명 환경변수가 없으면 서명 인자가 붙지 않는다 | Given 서명 관련 환경변수가 모두 비었을 때, When Windows/macOS 서명 인자 생성 함수를 각각 호출하면, Then None/빈 리스트를 돌려주고 빌드는 미서명으로 계속 진행된다. |
| Test-15 | 단위 | 자동 | 서명 환경변수가 있으면 플랫폼별 인자 체계로 조립된다 | Given Windows 서명 환경변수가 설정되면, When 인자를 조립하면, Then --signParams 문자열이 만들어지고 비밀번호가 로그 출력에서 마스킹된다. macOS 환경변수가 설정되면 --signAppIdentity/--signInstallIdentity/--notaryProfile 형태의 인자 리스트가 만들어진다(--signParams가 아니다). |
| Test-16 | 단위 | 자동 | 릴리스 저장소 URL이 단일 출처이고 origin과 일치한다 | Given 앱 모듈과 빌드 스크립트가, When 각자가 쓰는 저장소 URL을 비교하면, Then 두 값이 동일하고 https://github.com/thsvkd/naver-post-crawler 이다(옛 이름 naver-blog-crawler가 소스 어디에도 남아 있지 않다). |
| Test-17 | 단위 | 자동 | velopack_update를 import해도 네이티브 모듈이 로드되지 않는다 | Given 깨끗한 인터프리터에서, When naver_post_crawler.velopack_update를 import하면, Then sys.modules에 'velopack'이 들어 있지 않다(지연 임포트 보장 — 첫 화면 지연 회귀 차단). |
| Test-18 | 단위 | 자동 | 다운로드 진행률이 0~100에서 0.0~1.0으로 환산된다 | Given velopack이 0, 50, 100을 순서대로 보고할 때, When download()의 progress_cb가 호출되면, Then 콜백은 0.0, 0.5, 1.0을 받는다(기존 GUI progress_cb 시그니처와 호환). |
| Test-19 | 단위 | 자동 | 래퍼가 예외를 삼켜 앱 기동을 막지 않는다 | Given velopack 호출이 예외를 던지는 상황에서, When run_startup_maintenance()와 is_installed()를 호출하면, Then 예외가 밖으로 전파되지 않고 각각 조용히 반환/False를 돌려주며 로그만 남는다. |
| Test-20 | 단위 | 자동 | 시작 워커가 유지보수를 먼저 수행한다 | Given GUI가 기동할 때, When _auto_check_updates 워커가 실행되면, Then run_startup_maintenance()가 업데이트 확인보다 먼저 호출된다(낡은 nupkg 정리 지점 누락 방지). 그리고 이 호출은 UI 스레드가 아니라 워커 스레드에서 일어난다. |
| Test-21 | 단위 | 자동 | 새 버전 발견 시 버튼 라벨이 실제로 갱신된다 | Given check()가 새 버전을 돌려줄 때, When _check_updates가 실행되면, Then update_btn.content가 새 버전 문구로 갱신된다(flet 0.85의 ft.Button에는 text 필드가 없으므로 .text 대입은 아무 효과가 없다 — 실측 확인됨). |
| Test-22 | 단위 | 자동 | macOS에서도 업데이트 적용 경로가 차단되지 않는다 | Given sys.platform이 'darwin'이고 설치본으로 인식되는 상황에서, When _download_and_apply를 실행하면, Then '이 플랫폼에서는 자동 적용을 지원하지 않습니다' 분기로 빠지지 않고 download → apply_and_restart 경로로 진입한다. |
| Test-23 | 단위 | 자동 | 비설치 컨텍스트에서는 적용을 시도하지 않는다 | Given is_installed()가 False일 때, When 업데이트 적용을 시도하면, Then 다운로드/적용을 하지 않고 플랫폼에 맞는 안내 문구를 표시한다(macOS는 .app 번들 밖 실행이라는 별도 조건이 있으므로 Windows 기준 문구를 그대로 쓰지 않는다). |
| Test-24 | 단위 | 자동 | 적용 중 재클릭이 중복 다운로드를 시작하지 않는다 | Given _applying이 True인 상태에서, When 업데이트 버튼을 다시 클릭하면, Then 새 워커 스레드가 기동되지 않는다. 적용이 실패하면 _reset_applying으로 버튼과 가드가 복구되어 재시도할 수 있다. |
| Test-25 | 단위 | 자동 | 앱 데이터 경로가 번들 내부를 절대 쓰지 않는다 | Given FLET_APP_STORAGE_DATA가 없고 sys.frozen이 참인 상황에서, When app_data_dir()을 호출하면, Then 실행 파일 옆 storage/가 아니라 플랫폼별 사용자 데이터 경로(macOS: ~/Library/Application Support/…, Windows: %LOCALAPPDATA%\…)를 돌려준다. |
| Test-26 | 단위 | 자동 | 로그 디렉터리 기본값이 cwd 상대가 아니다 | Given GUI가 크롤링 옵션을 조립할 때, When 로그 디렉터리를 결정하면, Then Path('logs') 하드코딩이 아니라 앱 데이터 경로 아래의 절대 경로를 쓴다. |
| Test-27 | 통합 | 자동 | 출력 폴더 선택이 앱 데이터에 영속화된다 | Given 사용자가 출력 폴더를 선택하고 앱을 종료했다가 다시 열면, When GUI가 초기화되면, Then 이전에 선택한 폴더가 복원되고, 한 번도 선택한 적이 없으면 cwd 상대 'output'이 아니라 사용자 폴더 기반 절대 경로가 기본값이 된다. |
| Test-28 | 단위 | 자동 | Velopack packId와 앱 데이터 폴더 이름이 서로 다르다 | Given 빌드 스크립트의 packId 상수와 cookie.py의 앱 데이터 폴더 이름이, When 비교되면, Then 두 값이 다르다(Windows 기본 설치 경로가 %LocalAppData%\<PackId>\ 이므로 같으면 언인스톨 시 사용자 쿠키가 함께 삭제된다). |
| Test-29 | 단위 | 자동 | 쿠키 로그인 헬퍼가 명령행 인자로 기동되지 않는다 | Given 배포본 컨텍스트에서, When 헬퍼 실행 커맨드를 조립하면, Then argv에 헬퍼 플래그가 포함되지 않고 환경변수로 모드와 결과 경로가 전달된다(flet 러너의 '인자 있으면 개발자 모드' 분기 회피). 헬퍼 진입 판정도 같은 환경변수를 읽는다. |
| Test-30 | 통합 | 자동 | 커스텀 updater 잔재가 남아 있지 않다 | Given 이관이 끝난 저장소에서, When src/·tests/·scripts/를 검사하면, Then updater 모듈과 그 테스트 파일이 존재하지 않고 어떤 모듈도 그것을 import하지 않으며, naver_post_crawler.cli와 naver_post_crawler.gui가 정상 import된다. |
| Test-31 | 단위 | 자동 | 버전 SSOT가 일치한다 | Given pyproject.toml의 [project].version과 패키지 __version__이, When 비교되면, Then 두 값이 같다(sync_version 채택 시에도 '생성물이 최신인지' 검증으로 의미가 유지된다). |
| Test-32 | 단위 | 자동 | 업로드 에셋 선별이 이번 버전·이번 채널로만 좁혀진다 | Given 산출 디렉터리에 이전 버전 nupkg(vpk download가 델타 기준으로 받아 둔 것)와 이번 버전 산출물이 함께 있을 때, When 업로드 에셋 목록을 만들면, Then 이번 버전 파일과 해당 채널의 releases.<channel>.json만 선택되고 이전 버전 nupkg는 제외된다. |
| Test-33 | 단위 | 자동 | 릴리스 태그가 v<version> 형식으로 명시된다 | Given 배포 스크립트가, When 업로드 명령을 조립하면, Then 태그가 v{pyproject version} 형식으로 명시적으로 전달된다(도구 기본값은 'v' 없는 버전 문자열이라 기존 v0.1.0 관행과 어긋난다). |
| Test-34 | 단위 | 자동 | 릴리스 노트가 첫 플랫폼에서만 생성된다 | Given 이번 태그의 릴리스(draft 포함)가 이미 존재할 때, When 배포 스크립트를 두 번째 플랫폼에서 실행하면, Then 노트 생성 단계를 건너뛰고 에셋만 병합하며 기존 노트를 덮어쓰지 않는다. |
| Test-35 | 단위 | 자동 | 동일 버전 재배포가 차단된다 | Given 이전 정식 릴리스 태그와 pyproject 버전이 같을 때, When 배포 스크립트를 실행하면, Then 버전 게이트에서 실패하고 업로드를 시도하지 않는다. |
| Test-36 | 실기 | 실기 | [실기/Windows] flet build + 러너 패치가 실제로 컴파일된다 | Given Windows 개발 머신에서, When scripts/build.py를 실행하면, Then 패치된 main.cpp가 MSVC로 컴파일되고 flet build windows가 성공하며 vpk pack이 Setup.exe와 nupkg, releases.win.json을 산출한다. |
| Test-37 | 실기 | 실기 | [실기/Windows] 설치기가 훅 경고 없이 완료되고 창이 깜빡이지 않는다 | Given 산출된 Setup.exe를, When 실행해 설치하면, Then '설치가 부분적으로 성공했습니다' 경고가 뜨지 않고, 설치 직후 자동 실행된 앱의 첫 창이 1280x720에서 760x720으로 깜빡이지 않는다. |
| Test-38 | 실기 | 실기 | [실기/Windows] 설치본에서 OTA 확인→다운로드→적용→재시작이 완료된다 | Given 이전 버전이 설치된 Windows 기기에서 새 버전이 릴리스되었을 때, When 앱의 '업데이트 확인'을 누르면, Then 새 버전이 표시되고 다운로드 진행률이 갱신되며 적용 후 앱이 새 버전으로 재시작된다. |
| Test-39 | 실기 | 실기 | [실기/공통] 업데이트 적용 후 사용자 데이터가 잔존한다 | Given 쿠키·설정·백업 결과물이 있는 설치본에서, When 업데이트를 1회 적용하면, Then 재시작 후에도 저장된 쿠키로 로그인 상태가 유지되고 이전 백업 폴더의 txt와 .failures.json이 그대로 남아 증분 재개가 이어진다. |
| Test-40 | 실기 | 실기 | [실기/macOS] 미서명 vpk pack이 .pkg를 산출한다 | Given macOS 개발 머신에서 서명 환경변수 없이, When scripts/build.py를 실행하면, Then flet build macos가 .app을 만들고 vpk pack이 경고만 남긴 채 -osx-Setup.pkg, -osx-full.nupkg, releases.osx.json을 산출한다. |
| Test-41 | 실기 | 실기 | [실기/macOS] 다른 기기에서 설치·실행이 가능하다(Apple Silicon 포함) | Given 미서명 .pkg를 브라우저로 내려받은 다른 Mac(Apple Silicon 포함)에서, When 결정된 Gatekeeper 우회 절차를 따르면, Then 설치가 완료되고 앱이 SIGKILL 없이 실행된다(ad-hoc 서명 전략의 유효성을 여기서 확정한다). |
| Test-42 | 실기 | 실기 | [실기/macOS] Velopack 훅 전달 방식을 관측한다 | Given macOS .pkg 설치와 업데이트를 수행할 때, When 앱 기동 방식을 관측하면, Then 훅이 argv(--veloapp-*)로 오는지 환경변수(VELOPACK_FIRSTRUN/RESTART)로 오는지가 확정되고, argv로 온다면 창이 뜬 채 타임아웃되는지 확인해 대응(진입점 동기 훅 처리) 필요 여부를 판정한다. 관측 결과는 근거와 함께 문서에 기록한다. |
| Test-43 | 실기 | 실기 | [실기/공통] 배포본에서 앱 내 네이버 로그인이 동작한다 | Given 설치된 배포본에서, When '네이버 로그인'을 누르면, Then 빈 창이 아니라 실제 웹뷰 로그인 창이 뜨고 로그인 완료 후 세션 쿠키가 저장되어 카페 크롤링이 성공한다. (macOS는 웹뷰 백엔드 지원 범위 결정에 따라 이 항목의 적용 여부가 달라진다.) |
| Test-44 | 실기 | 실기 | [실기/공통] 한 태그에 두 채널 피드가 함께 올라간다 | Given Windows와 macOS에서 각각 빌드·업로드를 마쳤을 때, When GitHub 릴리스 v<version>을 확인하면, Then 하나의 공개 릴리스에 releases.win.json과 releases.osx.json이 모두 존재하고 두 OS의 설치본이 각자 자기 채널 피드로 업데이트를 확인한다. 중간 상태(첫 플랫폼만 올라간 시점)는 draft라 사용자에게 노출되지 않는다. |
| Test-45 | 실기 | 실기 | [실기/공통] 두 번째 릴리스에서 델타 패키지가 생성된다 | Given 이전 버전이 이미 릴리스된 상태에서, When 새 버전을 빌드하면, Then vpk download가 이전 full nupkg를 받아 오고 pack이 delta nupkg를 산출하며, 설치본이 전체 64MB가 아니라 델타만 내려받아 적용한다. |

## 6. 리스크 등록부

| 심각도 | 리스크 | 대응 |
|---|---|---|
| 높음 | flet build가 임베드하는 CPython 3.12와 소스의 Python 3.14 전용 문법(PEP 758)이 충돌해 배포본이 import 단계에서 즉사한다. 실측 확인: 3.12.11로 src/ 전체 compile 시 cookie_login.py:69가 SyntaxError, :130도 같은 형태. 개발 머신(3.14)에서는 절대 재현되지 않는다. | W-1을 최선행 작업으로 두고 두 줄을 괄호 형태로 수정한다. requires-python/.python-version/ruff target-version/uv.lock을 3.12 기준으로 맞추고, '3.12로 src/ 전체 compile' 회귀 테스트(Test-1)를 스위트에 넣어 재발을 막는다. 인터프리터 미존재 시 skip이 아니라 실패로 처리한다. |
| 높음 | flet 러너의 '인자 있으면 개발자 모드' 동작 때문에 쿠키 로그인 헬퍼 서브프로세스가 배포본에서 동작하지 않는다. 네이버 로그인은 카페 크롤링의 핵심 기능이라 앱 주요 기능이 죽는다. 게다가 분기 조건인 sys.frozen은 PyInstaller 전용 플래그라 flet build에서 False로 떨어져 잘못된 개발용 커맨드를 만든다. | Velopack 작업과 분리해 W-4에서 선결한다. 헬퍼 모드 전달을 argv 대신 환경변수로 바꾸고, frozen 판정도 flet build에서 유효한 신호로 교체한다. 단위 테스트로 '커맨드에 헬퍼 플래그가 없다'를 잠그고(Test-29), 실제 로그인 창은 Test-43 E2E로 검증한다. |
| 높음 | 출력 폴더와 로그가 cwd 상대라, 설치본에서 current\ 안에 쌓였다가 다음 업데이트에서 백업 결과물이 통째로 사라진다. Velopack 설치본은 바로가기로 실행되어 cwd가 보장되지 않으며, macOS는 .app 내부가 cwd가 되면 더 나쁘다. | W-5에서 GUI 기본 출력 폴더를 사용자 폴더로 바꾸고 마지막 선택을 앱 데이터에 영속화한다. gui.py:589의 Path('logs') 하드코딩을 앱 데이터 아래로 옮긴다. Test-26/27로 잠그고, 업데이트 1회 적용 후 데이터 잔존을 Test-39 실측 증거로 남긴다. |
| 높음 | 미서명·미공증 macOS 배포가 Velopack 공식 요구사항과 정면 충돌한다. 문서가 'Code signing and notarization is required by Apple ... or your app won't run'이라고 명시하고, macOS Sequoia(15)부터 Control-클릭 우회가 제거되어 일반 사용자가 스스로 넘기 어렵다. 'App is damaged' 문구 때문에 앱이 망가진 것으로 오해할 수도 있다. | 패키징 자체는 막히지 않는다(vpk는 경고만 내고 통과). 이관 초기에 Test-40/41을 실측해 다른 기기에서 설치·실행이 되는지 확인하고, 실패 시 macOS 배포 방식을 즉시 재검토한다. README/릴리스 노트에 시스템 설정 → 개인정보 보호 및 보안 → '확인 없이 열기' 경로와 xattr 대안을 한국어로 명문화한다. sign.py에 서명 인자 자리를 미리 뚫어 두면 나중에 환경변수만 채워 전환할 수 있다. |
| 높음 | Apple Silicon은 실행되는 모든 Mach-O에 최소 ad-hoc 서명을 요구하는데, Velopack이 이미 서명된 .app에 UpdateMac과 sq.version 심볼릭 링크를 추가해 기존 번들 서명을 무효화한다. --signAppIdentity가 없으면 Velopack은 재서명을 아예 하지 않으므로(경고 후 스킵) arm64 Mac에서 앱 또는 UpdateMac이 실행 즉시 SIGKILL 될 수 있다. | D-3에서 전략을 확정하고 Test-41로 실측한다. 후보는 (a) vpk pack 이후 빌드 스크립트에서 codesign --force --deep --sign - 로 ad-hoc 재서명, (b) --signAppIdentity "-"를 넘겨 Velopack 내부 재서명 경로를 태우기. 어느 쪽이든 Intel/Apple Silicon 양쪽에서 설치→실행→업데이트까지 확인해야 한다. |
| 중간 | macOS에서 Velopack 라이프사이클 훅이 argv로 전달될 경우 대응책이 없다. macOS 러너는 dartEntrypointArguments를 설정하지 않아 파이썬이 정상 기동하는데, 그러면 훅 인자를 받고도 Flutter+파이썬을 전부 띄우고 창까지 보여준 뒤 30초/15초 타임아웃으로 kill 된다. Windows식 네이티브 조기 종료 패치를 적용할 지점 자체가 없다. | velopack.abi3.so strings 실측 결과 --veloapp-* argv 문자열과 VELOPACK_FIRSTRUN/VELOPACK_RESTART env 문자열이 둘 다 존재하고, macOS .pkg의 postinstall은 env 방식이므로 argv 훅이 오지 않을 가능성이 높다. Test-42로 실측 확정한다. argv로 온다면 velopack App().run()을 src/main.py 최상단 동기 호출로 올리는 배치를 설계한다(네이티브 argv를 직접 읽으므로 Dart를 거치지 않는다). 기동 속도와의 절충은 그때 판단한다. |
| 중간 | Windows 러너 main.cpp 패치의 앵커가 flet 업그레이드로 깨지거나, 반대로 깨진 채 조용히 넘어간다. flet이 캐시된 Flutter 프로젝트를 재사용하면(_PATCH_REVISION 미갱신) 패치를 고쳐도 옛 main.cpp로 빌드되는 유령 버그가 난다. 증상은 설치기의 '설치가 부분적으로 성공했습니다' 경고와 창 깜빡임이며 macOS 개발 머신에서는 재현되지 않는다. | 앵커가 정확히 1회가 아니면 ValueError로 빌드를 실패시키는 fail-loud 규약을 유지하고(Test-7), 캐시 디렉터리 이름에 패치 리비전+창 크기를 인코딩하는 규칙을 테스트로 잠근다(Test-8). flet 버전은 pyproject에서 핀하고 상향을 별도 작업으로 분리한다. 다만 이 테스트는 하드코딩된 발췌 문자열을 쓰므로 실제 새 템플릿 변경은 못 잡는다 — flet 상향 시 build/_flet_template 캐시를 지우고 재빌드해 fail 여부를 확인하는 절차를 릴리스 체크리스트에 넣는다. |
| 중간 | flet-desktop과 flet-cli가 하한(>=0.85.1)만 걸려 있어 빌드 시점에 버전이 어긋나면 '앱은 뜨는데 화면이 안 그려지는 빈 창'이 된다. flet pack에서는 런타임 flet과 UI 클라이언트가 같은 프로세스라 드러나지 않던 실패 모드이므로 전환 직후 처음 마주칠 가능성이 높다. | W-6에서 두 패키지를 동일 버전 ==로 못박고(현재 lock은 둘 다 0.85.1) 버전 상향 시 항상 함께 올린다는 규칙을 pyproject 주석에 남긴다. |
| 중간 | GithubSource가 per_page=10&page=1을 하드코딩해 최근 10개 릴리스만 스캔한다. 플랫폼별로 태그를 나누면 가시 범위가 5버전으로 반감되고, 해당 채널 피드가 11번째 이후로 밀리면 업데이트 확인이 조용히 실패한다(예외를 삼키고 건너뜀). 또 이 모델에 draft 필드가 없어 draft 필터링을 하지 않는다. | win/osx를 같은 태그 한 릴리스로 통합한다(채널 접미사로 파일명이 분리되어 충돌하지 않는다). 실패한 배포로 생긴 draft 릴리스는 즉시 삭제하는 절차를 배포 체크리스트에 넣는다. |
| 중간 | Velopack packId를 데이터 폴더 이름과 같게 잡으면 %LocalAppData%\<PackId>\가 설치 루트와 데이터 폴더로 겹쳐, 언인스톨 시 사용자 쿠키가 함께 삭제된다. packId는 설치 폴더·캐시 경로·업데이트 식별자로 쓰여 나중에 바꾸면 기존 설치본과 연결이 끊긴다. | D-4에서 packId를 지금 확정하고 데이터 폴더 이름과 다르게 정한다. 두 상수가 서로 다름을 단언하는 테스트를 둔다(Test-28). packId는 NuGet ID 규칙, packVersion은 semver2(4자리 불가)를 만족해야 한다. |
| 중간 | 이미 배포된 v0.1.0 포터블 사용자(다운로드 4건 실측)가 Velopack 전환 후 영구히 방치된다. 구 updater는 정확히 naver-post-crawler-<target>.zip 이름의 에셋만 찾고 없으면 '최신 버전입니다'로 표시하므로, 새 버전이 나온 사실조차 알 수 없다. | D-11에서 이관 방식을 확정한다. 권장은 전환 안내용 포터블 릴리스를 한 번 더 내고 그 앱이 '설치본으로 이전하세요'를 띄우는 것이다. Setup.exe를 옛 에셋 이름으로 위장 배포하는 방법은 기술적으로는 동작하지만(extract가 최상위 엔트리 1개만 검사) 앱 자리에 설치 마법사가 놓이고 잔재 파일이 남아 비권장. 최소한 README/릴리스 노트 상단에 이전 안내를 고정한다. |
| 중간 | tests/test_updater.py 490줄(테스트 28개)이 통째로 무의미해진다. Velopack이 버전 비교·에셋 선택·SHA256·사이드카 로직을 전부 네이티브 라이브러리에 위임하므로 1:1 대체가 없고, 삭제하면 그만큼 자동 검증 커버리지가 사라진다. 자동 업데이트는 실배포 전에는 검증이 어려운 영역이다. | 삭제 대신 velopack_update 래퍼 자체(예외 삼킴, 진행률 환산, is_installed 가드, 지연 임포트)를 대상으로 하는 축소된 테스트를 새로 쓴다(Test-17~19). 실제 설치·델타·재시작은 Test-38/44/45의 E2E 실측 항목으로 명시적으로 잡는다. 테스트 케이스 리스트 변경은 인간만 닫을 수 있는 게이트이므로 D-6 승인이 선행돼야 한다. |
| 중간 | 두 플랫폼 배포에서 릴리스 노트가 두 번 생성되거나 서로 다른 내용이 만들어진다. claude -p는 두 머신에서 각각 다른 결과를 낼 수 있고, gh release create는 같은 태그가 이미 있으면 실패해 두 번째 플랫폼 업로드가 그대로 깨진다. | vpk upload github --merge를 쓰면 노트(body)는 draft 생성 경로에서만 쓰이므로 두 번째 업로드가 노트를 건드리지 않는 것이 구조적으로 보장된다. gh를 유지한다면 create/upload 폴백 분기를 직접 구현한다. 어느 쪽이든 '이번 태그의 draft가 이미 있으면 노트 생성 스킵' 게이트를 스크립트에 명시하고 Test-34로 잠근다. |
| 중간 | velopack 파이썬 패키지 버전과 vpk CLI 버전이 어긋나면 nupkg/피드 포맷 불일치로 설치본이 업데이트를 못 받는 조용한 실패가 날 수 있다. 참조의 _find_vpk()는 존재 여부만 확인하고 버전은 보지 않는다. | _find_vpk()에 vpk --version 확인을 추가해 pyproject의 velopack 핀과 메이저/마이너가 다르면 경고 또는 실패한다. velopack을 >= 대신 ==로 핀하는 것도 검토한다(flet-desktop/flet-cli를 묶는 것과 같은 이유). |
| 중간 | macOS 산출 아키텍처가 arm64 전용이면 Intel Mac 사용자가 실행할 수 없는 패키지를 받게 된다. Velopack 기본 osx 채널은 아키텍처를 구분하지 않는다. | flet build macos 산출물을 lipo -archs로 실측해 universal 여부를 확인한다. universal이 아니고 Intel을 지원한다면 채널을 osx-arm64/osx-x64로 분리하고 각 피드를 같은 릴리스에 올린다(채널이 다르므로 --merge로 병합 가능). D-13에서 지원 범위를 먼저 정한다. |
| 중간 | macOS에서 /Applications에 설치한 사용자는 업데이트마다 osascript 관리자 암호 프롬프트를 만난다. 대화상자 문구 커스터마이즈 불가, Touch ID 미지원이라 사용자가 악성 동작으로 오해하고 취소할 수 있다. | 설치기 문구(--instWelcome/--instConclusion)에 '나만 사용(~/Applications)'을 권장 선택지로 안내하고, 앱 내 업데이트 UI에서 적용 직전에 '관리자 암호를 물을 수 있습니다'를 고지한다. D-2에서 권장 설치 위치를 확정한다. |
| 낮음 | 빌드 시점 네트워크 의존과 시스템 도구 부재. flet 템플릿 zip을 GitHub에서 직접 받고(캐시는 build/ 아래라 clean하면 사라짐), Velopack은 Unix에서 번들 zstd가 아니라 시스템 zstd를 요구한다. | scripts/setup.py에 vpk·Flutter·Xcode/VS·zstd 선행 조건 점검을 추가하고 없으면 설치 명령을 안내한다. 다운로드 실패 시 원인·URL을 명시해 실패하는 동작을 유지한다. |
| 낮음 | 빌드 백엔드 차이(uv_build vs 참조의 hatchling)로 인한 미검증 영역. flet build는 [project].dependencies만 requirements.txt로 변환하고 src/를 복사하므로 백엔드 무관일 가능성이 높지만 확인된 바는 아니다. | flet build를 1회 실행해 번들 site-packages에 이 앱 자신의 dist-info가 없고 src/가 복사되는지 확인한다(그래야 __version__ 하드코딩이 유일한 런타임 버전 소스라는 전제가 성립한다). 문제가 있으면 백엔드 교체를 검토한다. |

## 7. 관련 코드 포인터

| 맥락 | 위치 | 메모 |
|---|---|---|
| 차단 B-1 | `src/naver_post_crawler/cookie_login.py:69,130` | PEP 758 괄호 없는 except 튜플 — 3.12에서 SyntaxError |
| 차단 B-2 | `src/naver_post_crawler/cookie_login.py:84-90` | _helper_command() 가 앱 실행파일을 HELPER_FLAG 인자와 함께 재실행 |
| 차단 B-3 | `src/naver_post_crawler/gui.py:117-119,583,589 / cli.py:78-83,130-136` | 출력·로그 기본값이 cwd 상대 |
| 경로 정책 | `src/naver_post_crawler/cookie.py:142-165` | app_data_dir() — frozen 폴백이 Velopack 교체 모델과 충돌 |
| 창 크기 SSOT | `src/naver_post_crawler/gui.py:97-100` | window.width/height 인라인 → 모듈 상수 승격 필요(flet_template 정규식 전제) |
| 살아있는 버그 | `src/naver_post_crawler/gui.py:397` | ft.Button 에 text 필드 없음(flet 0.85.1 실측) → .content 로 수정 |
| 살아있는 버그 | `src/naver_post_crawler/updater.py:39-40` | REPO_NAME='naver-blog-crawler' ≠ origin 'naver-post-crawler' |
| 폐기 대상 | `src/naver_post_crawler/updater.py (415줄) / tests/test_updater.py (490줄)` | D-6 으로 폐기 승인됨 |
| 참조 구현 | `../yt-knowledge-extractor/scripts/{_common,build,deploy,sign,flet_template}.py` | 이식 원본 |
| 참조 구현 | `../yt-knowledge-extractor/src/yke/velopack_update.py` | 앱 측 Velopack 래퍼 원본 |
| 참조 구현 | `../yt-knowledge-extractor/tests/test_flet_template.py` | 러너 패치 회귀 테스트 원본 |

## 8. PRD


# PRD — naver-post-crawler 배포/자동업데이트 이관 (flet pack → flet build + Velopack)

## 1. 문제

현재 배포 경로는 `scripts/build.py`의 `flet pack`(PyInstaller) 단일 실행파일 → zip → GitHub Releases 에셋이고, 자동 업데이트는 `src/naver_post_crawler/updater.py`(415줄)의 커스텀 사이드카 방식이다. 세 가지 한계가 있다.

1. **자동 업데이트 적용이 Windows 전용이다.** `updater.py`의 `apply_and_restart()`가 `sys.platform != "win32"`이면 `RuntimeError`를 던지고, `gui.py:428-437`도 같은 분기로 macOS 사용자를 막는다. 에셋 이름 규칙(`naver-post-crawler-{windows|macos|linux}.zip`)만 크로스 플랫폼이고 실제 적용 경로가 없다.
2. **업데이트 인프라를 자체 유지보수하고 있다.** 버전 비교·에셋 선택·SHA256 검증·사이드카 스크립트 생성·롤백을 전부 직접 구현했고, 이를 지키느라 `tests/test_updater.py` 490줄(테스트 28개)을 함께 끌고 간다. 델타 업데이트는 없어 매 업데이트마다 64MB 전체를 내려받는다(v0.1.0 에셋 실측).
3. **참조 프로젝트(yt-knowledge-extractor)가 이미 같은 문제를 flet build + Velopack으로 풀어 두었다.** 동일한 구조를 두 벌 유지할 이유가 없다.

## 2. 목표

- 빌드 경로를 `flet build <target>`로 통일하고 `flet pack` 경로를 제거한다.
- 설치/자동업데이트를 Velopack(vpk)으로 통일하고 커스텀 `updater.py`를 제거한다.
- **Windows와 macOS를 모두 정식 지원한다.** 두 산출물을 같은 GitHub 릴리스 태그에 올려 각 OS가 자기 채널(`releases.win.json` / `releases.osx.json`) 피드를 보게 한다.
- 코드 서명·공증은 하지 않되(미서명 배포), 참조 `scripts/sign.py`처럼 환경변수로 서명 인자를 주입할 수 있는 구조는 남긴다.

## 3. 이관 전 반드시 선결해야 하는 것(조사에서 새로 드러난 차단 요소)

이 세 건은 Velopack과 무관하게 **flet build 전환 자체를 막는다.** 실측 근거를 첨부한다.

### 3-1. 임베드 인터프리터가 CPython 3.12다 (CRITICAL, 실측)

참조 프로젝트의 실제 빌드 산출물 `../yt-knowledge-extractor/build/flutter/build/cpython-3.12.9+20250205-aarch64-apple-darwin-install_only_stripped.tar.gz`가 증거다. 반면 이 저장소는 `pyproject.toml:7` `requires-python = ">=3.14"`, `[tool.ruff] target-version = "py314"`를 전제한다.

uv의 CPython 3.12.11로 `src/` 전체를 `compile()`한 실측 결과:

```
[('src/naver_post_crawler/cookie_login.py', 69, 'multiple exception types must be parenthesized')]
```

`src/naver_post_crawler/cookie_login.py:69` `except json.JSONDecodeError, TypeError, ValueError:` 와 `:130` `except ValueError, IndexError:` 는 Python 3.14의 PEP 758(괄호 없는 except 튜플)이다. `flet pack`은 호스트의 3.14를 그대로 묶었기 때문에 지금까지 드러나지 않았으나, **flet build로 가는 순간 배포본이 import 단계에서 죽는다.** 개발 머신(3.14)에서는 절대 재현되지 않는다.

### 3-2. flet build 러너는 argv가 있으면 파이썬을 아예 실행하지 않는다 (CRITICAL)

flet 빌드 템플릿의 `lib/main.dart`가 `_args.isNotEmpty && isDesktopPlatform()`이면 '개발자 모드'로 판정해 `runPythonApp()`을 호출하지 않는다. 인자를 Dart로 넘기는 러너는 `windows/runner/main.cpp`와 `linux/my_application.cc`뿐이고 `macos/`에는 없다.

이 저장소에서 두 가지가 걸린다.
- **Velopack Windows 라이프사이클 훅**(`--veloapp-install/-updated/-obsolete/-uninstall`): 파이썬이 안 뜨므로 훅을 처리할 수 없다 → 설치기가 "설치가 부분적으로 성공했습니다" 경고. 참조는 `scripts/flet_template.py`로 `main.cpp`를 패치해 네이티브 진입점에서 조기 종료시켰다.
- **이 앱 고유 문제 — 쿠키 로그인 헬퍼**: `src/naver_post_crawler/cookie_login.py:84-90`의 `_helper_command()`가 `getattr(sys, "frozen", False)`로 분기해 앱 실행 파일을 `HELPER_FLAG`("--__cookie-login") 인자와 함께 재실행한다. flet build 산출물에서는 (a) `sys.frozen`이 서지 않아 잘못된 개발용 분기를 타고, (b) 설령 앱 exe를 인자와 함께 부르더라도 위 개발자 모드 분기에 걸려 로그인 창 대신 빈 FletApp이 뜬다. **네이버 로그인은 카페 크롤링의 핵심 기능이므로 이관 완료 조건에 포함된다.**

`velopack.abi3.so` strings 실측 결과 `--veloapp-*` 네 문자열과 `VELOPACK_FIRSTRUN`/`VELOPACK_RESTART`가 **둘 다** 존재한다. macOS .pkg의 postinstall은 `env VELOPACK_FIRSTRUN=1 open ...` 방식이므로 Windows식 argv 하이재킹 문제는 macOS에 없을 가능성이 높으나, 문서만으로 확정 불가이므로 실측 항목으로 남긴다(Test-40).

### 3-3. 앱 데이터·출력물 경로가 Velopack 교체 모델과 충돌한다 (HIGH)

`src/naver_post_crawler/cookie.py:142-165`의 `app_data_dir()`은 `FLET_APP_STORAGE_DATA` → `sys.frozen`이면 실행 파일 옆 `storage/` → 플랫폼별 순으로 분기하고, docstring이 "updater가 exe를 교체해도 이 폴더는 그대로 유지된다"고 단언한다. Velopack은 Windows에서 `%LocalAppData%\<PackId>\current\`를, macOS에서 `.app` 번들을 **통째로 교체**하므로 이 전제가 깨진다.

더 큰 문제는 쿠키가 아니라 **출력 폴더와 로그**다. 둘 다 cwd 상대다.
- `gui.py:117-119` 출력 폴더 기본값 `"output"`, `gui.py:583` `Path(self.out_field.value.strip() or "output")`
- `gui.py:589` `"log_dir": Path("logs")` — 사용자가 바꿀 수도 없는 하드코딩
- `cli.py:78-83` `-o` 기본 `Path("output")`, `cli.py:130-136` `--log-dir` 기본 `Path("logs")`

포터블 zip에서는 "실행한 폴더 안"이라는 직관이 성립했지만, Velopack 설치본은 바로가기로 실행되어 cwd가 `current\` 또는 시스템 폴더가 된다 → 백업 결과물이 다음 업데이트에서 통째로 사라진다.

## 4. 범위

### 4-1. 포함

- Python 3.12 호환성 확보 및 회귀 테스트(§3-1)
- 쿠키 로그인 헬퍼 기동 방식 재설계(§3-2)
- 앱 데이터/출력/로그 경로 정책 정리(§3-3)
- `scripts/`: `_common.py` 보강, `flet_template.py`·`sign.py`·`deploy.py` 신설, `build.py` 전면 재작성
- `src/naver_post_crawler/velopack_update.py` 신설, `updater.py`·`tests/test_updater.py` 제거
- `gui.py` 업데이트 계층 교체(Windows 전용 차단 분기 제거 포함), `cli.py --check-update` 처리
- `pyproject.toml` 의존성 재정비(flet-desktop == flet-cli 핀, velopack 추가, pyinstaller 제거)
- 릴리스 저장소 URL 정정 및 단일 출처화
- `.gitignore`·`README.md`·`docs/SPEC.md` 갱신(배포·자동업데이트 장 신설)

### 4-2. 비범위(이번에 하지 않는 것)

- **코드 서명·공증**(확정 결정 4). sign.py의 환경변수 자리만 뚫어 둔다.
- **GitHub Actions CI 도입.** 수동 2단계 배포(Windows 머신 → macOS 머신)를 먼저 실측 검증하고, 그 스크립트를 감싸는 형태로 나중에 얹는다. macOS 러너 과금 배율 10배와 릴리스 노트 생성의 `claude -p` 로그인 의존이 이유다.
- **Linux 지원.** 현재 코드 여러 곳에 linux 분기가 있으나 배포 대상이 아니다(제외 여부는 인간 결정 사항).
- **기존 v0.1.0 포터블 사용자의 데이터 자동 마이그레이션.** 포터블 쿠키는 `<exe폴더>/storage/cafe_cookie.txt`에 있는데 설치본은 그 위치를 알 방법이 없다. 쿠키는 만료되는 세션이고 '네이버 로그인' 버튼으로 재취득 가능하므로 "설치 후 1회 재로그인" 안내로 갈음한다. 백업 txt와 `.failures.json`은 사용자가 지정한 폴더에 남으므로 같은 폴더를 다시 지정하면 증분 재개가 그대로 이어진다(`writer.saved_log_nos`가 폴더 스캔 기반, `writer.py:61-70`).
- **프리릴리스 채널 운영, GithubSource access_token.**

## 5. 확인된 사실(추측 아님) — 설계 근거

- **origin은 `https://github.com/thsvkd/naver-post-crawler.git`이다**(`git remote -v` 실측). `updater.py:39-40`의 `REPO_NAME = "naver-blog-crawler"`는 옛 이름이며, `api.github.com/repos/thsvkd/naver-blog-crawler/...`가 301 리다이렉트되어 우연히 동작 중이다. Velopack `GithubSource`가 301을 따라간다는 보장이 없으므로 정식 이름으로 고쳐야 한다. README.md:7,36 링크도 같다.
- **`ft.Button`에 `text` 필드가 없다**(설치된 flet 0.85.1에서 `dataclasses.fields` 실측: `has text: False | has content: True`). 즉 `gui.py:397`의 `self.update_btn.text = f"v{...} 로 업데이트 후 재시작"`은 **현재 살아 있는 버그**이고, 새 버전을 찾아도 버튼 라벨이 바뀌지 않는다. 참조는 `.content`로 고쳐 두었다.
- **`[tool.flet.app]`은 이미 준비돼 있다**(`pyproject.toml:51-57` `module = "main"`, `path = "src"` + `src/main.py` 셔임). 그대로 유효하다.
- **창 크기 SSOT가 없다.** `gui.py:97-98`이 `page.window.width = 760` / `height = 720`으로 `_build()` 안에 인라인이라, 참조 `flet_template.py`의 정규식 `^_WINDOW_(WIDTH|HEIGHT)\s*=\s*(\d+)\b`가 매칭 실패해 즉시 `fail()`한다. 모듈 상수 승격이 선행돼야 한다.
- **Velopack 채널 기본값이 OS 이름이라 두 플랫폼 산출물의 파일명이 충돌하지 않는다.** macOS는 `*-osx-Setup.pkg`, `*-0.2.0-osx-full.nupkg`, `releases.osx.json`; Windows는 `*-win-Setup.exe`, `*-0.2.0-full.nupkg`(win 기본 채널만 nupkg 접미사 예외), `releases.win.json`. 따라서 **같은 태그 한 릴리스에 통합**하는 것이 정답이다.
- **`GithubSource`는 `/releases/latest`가 아니라 `per_page=10&page=1`로 최근 10개 릴리스를 스캔**해 각 릴리스의 `releases.{channel}.json`을 모아 하나의 피드로 합친다(없으면 조용히 건너뜀). 플랫폼별 태그 분리는 이 창을 두 배로 소진하므로 비권장.
- **`vpk upload github --merge`가 draft 생성/재사용/중복 방지를 이미 구현**하고 있다. 첫 업로드는 항상 draft이고 `--publish`로 공개된다. 같은 채널 피드가 이미 있으면 거부하므로 같은 플랫폼 이중 업로드만 정확히 막힌다. 릴리스 노트(body)는 draft 생성 경로에서만 쓰이므로 두 번째 업로드가 노트를 덮어쓰지 않는다.
- **플랫폼별 빌드 머신 제약은 회피 불가.** `uv run flet build macos --show-platform-matrix` 실측 결과 `flet build windows`는 Windows에서만, `flet build macos`는 macOS에서만 실행 가능하다. vpk도 macOS 패키지는 macOS에서만 만들 수 있다(codesign/xcrun/productbuild 의존).
- **미서명 macOS 패키징 자체는 실패하지 않는다.** `OsxPackCommandRunner.CodeSign`은 `--signAppIdentity`가 비면 경고만 찍고 통과한다. 다만 공식 문서는 서명·공증을 필수로 못박고 있고, macOS Sequoia(15)부터 Control-클릭 우회가 제거되어 사용자는 시스템 설정에서 "확인 없이 열기"를 눌러야 한다.
- **`velopack` 파이썬 바인딩은 abi3 네이티브 확장이고 import만으로 0.5초 이상 걸린다.** win32/amd64/arm64와 macosx x86_64/arm64 휠이 모두 있다. 반드시 함수 안에서 지연 임포트하고 워커 스레드에서만 호출해야 한다.
- **`FLET_APP_STORAGE_DATA`는 flet 러너가 프로덕션 모드에서 실제로 세팅한다.** 따라서 `app_data_dir()`의 첫 분기가 잡혀 쿠키는 살아남는다 — 다만 이는 flet 런타임 동작에 대한 암묵 의존이므로 frozen 폴백 분기를 제거하고 회귀 테스트로 고정해야 한다.

## 6. 완료 정의

§7의 `Test-N` 인수 기준 전체 통과. 자동화 가능 항목은 `scripts/test.py`(ruff 린트 + 포맷 검사 + pytest)로, 실기 항목은 Windows/macOS 실측 증거 번들로 확인한다.
