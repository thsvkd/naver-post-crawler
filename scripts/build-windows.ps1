# Windows 네이티브 앱(.exe) 빌드 스크립트. **Windows에서 실행**한다.
#
# 사전 준비(일회성):
#   1) uv            : https://docs.astral.sh/uv/
#   2) Flutter SDK   : https://docs.flutter.dev/get-started/install/windows
#   3) Visual Studio : "Desktop development with C++" 워크로드
#
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
#
# 결과물: build\windows\ 아래 실행파일. (개발 중 빠른 실행은 'uv run naver-blog-crawler-gui')
#
# 참고: Flutter 툴체인 설치가 부담되면 flet build 대신 PyInstaller 기반
#       'uv run flet pack src\naver_blog_crawler\gui.py' 로 단일 exe를 만들 수 있다.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "==> 의존성 동기화 (gui 포함)"
uv sync --extra gui

Write-Host "==> flet build windows"
uv run flet build windows `
    --module-name naver_blog_crawler.gui `
    --product "Naver Blog Backup" `
    --org com.thsvkd

Write-Host "==> 완료: build\windows\ 를 확인하세요."
