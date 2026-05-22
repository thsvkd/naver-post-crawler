#!/usr/bin/env bash
# macOS / Linux 네이티브 앱 빌드 스크립트.
# 실행한 OS를 감지해 'flet build macos' 또는 'flet build linux'를 수행한다.
#
# 자동 처리:
#   - Linux: clang/cmake/ninja/pkg-config/GTK3 개발 패키지를 패키지 매니저로 설치한다.
#            (root가 아니면 sudo를 사용하며, sudo가 없으면 설치를 건너뛰고 안내한다.)
#   - macOS: CocoaPods를 Homebrew로 설치한다. Xcode CLT가 없으면 빌드 불가이므로 중단한다.
#   - Flutter SDK는 flet build가 필요 시 자동으로 내려받는다.
#
# 사용:
#   scripts/build-unix.sh
#
# 결과물: build/<platform>/ 아래 앱 번들. (개발 중 빠른 실행은 'uv run naver-blog-crawler-gui')
#
# 참고: Flutter 툴체인 설치가 부담되면 flet build 대신 PyInstaller 기반
#       'uv run flet pack src/naver_blog_crawler/gui.py' 로 단일 실행파일을 만들 수 있다.
set -euo pipefail

# 저장소 루트로 이동(스크립트 위치 기준).
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "오류: uv가 설치되어 있지 않습니다. https://docs.astral.sh/uv/ 를 참고하세요." >&2
  exit 1
fi

install_linux_deps() {
  # flet(Flutter) Linux 데스크톱 빌드에 필요한 네이티브 패키지.
  # 권한 상승 명령을 정한다(root면 불필요, sudo 없으면 설치 생략).
  local sudo=""
  if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
      sudo="sudo"
    else
      echo "경고: root가 아니고 sudo도 없어 빌드 의존성 자동 설치를 건너뜁니다." >&2
      echo "      clang, cmake, ninja, pkg-config, GTK3 개발 패키지를 수동 설치하세요." >&2
      return 0
    fi
  fi

  if command -v apt-get >/dev/null 2>&1; then
    echo "==> Linux 빌드 의존성 설치 (apt)"
    $sudo apt-get update
    $sudo apt-get install -y clang cmake ninja-build pkg-config libgtk-3-dev
  elif command -v dnf >/dev/null 2>&1; then
    echo "==> Linux 빌드 의존성 설치 (dnf)"
    # pkgconf-pkg-config는 최신 Fedora/RHEL 기준. 구형은 pkgconfig 가상 패키지일 수 있다.
    $sudo dnf install -y clang cmake ninja-build pkgconf-pkg-config gtk3-devel
  elif command -v pacman >/dev/null 2>&1; then
    echo "==> Linux 빌드 의존성 설치 (pacman)"
    $sudo pacman -S --needed --noconfirm clang cmake ninja pkgconf gtk3
  else
    echo "경고: 지원되는 패키지 매니저(apt/dnf/pacman)를 찾지 못했습니다." >&2
    echo "      clang, cmake, ninja, pkg-config, GTK3 개발 패키지를 수동 설치하세요." >&2
  fi
}

install_macos_deps() {
  # Xcode Command Line Tools가 없으면 macOS 앱 빌드 자체가 불가하므로 중단한다.
  if ! xcode-select -p >/dev/null 2>&1; then
    echo "오류: Xcode Command Line Tools가 없습니다. 'xcode-select --install' 후 다시 시도하세요." >&2
    exit 1
  fi
  if ! command -v pod >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
      echo "==> CocoaPods 설치 (brew)"
      brew install cocoapods
    else
      echo "오류: CocoaPods(pod)가 없고 Homebrew도 없습니다." >&2
      echo "      'sudo gem install cocoapods' 등으로 설치 후 다시 시도하세요." >&2
      exit 1
    fi
  fi
}

os="$(uname -s)"
case "$os" in
  Darwin)
    platform="macos"
    install_macos_deps
    ;;
  Linux)
    platform="linux"
    install_linux_deps
    ;;
  *)
    echo "오류: 지원하지 않는 OS입니다: $os (Windows는 scripts/build-windows.ps1 사용)" >&2
    exit 1
    ;;
esac

echo "==> 의존성 동기화 (gui 포함)"
uv sync --extra gui

echo "==> flet build $platform"
uv run flet build "$platform" \
  --product "Naver Blog Backup" \
  --org com.thsvkd

# flet이 에러를 내고도 0으로 끝나는 경우가 있어 결과물이 실제로 생성됐는지 확인한다.
if [ ! -d "build/$platform" ] || [ -z "$(ls -A "build/$platform" 2>/dev/null)" ]; then
  echo "오류: 빌드가 끝났지만 build/$platform 에 결과물이 없습니다." >&2
  exit 1
fi

echo "==> 완료: build/$platform/ 를 확인하세요."
