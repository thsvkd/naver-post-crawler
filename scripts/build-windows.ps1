# Windows 네이티브 앱(.exe) 빌드 스크립트. **Windows에서 실행**한다.
#
# 자동 처리:
#   - Visual Studio "Desktop development with C++" 워크로드가 없으면 winget으로 설치한다.
#   - Flutter SDK는 flet build가 필요 시 자동으로 내려받는다.
#
# 사전 준비(일회성):
#   1) uv     : https://docs.astral.sh/uv/
#   2) winget : Windows 11에 기본 포함(App Installer). VS 자동 설치에 사용.
#
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
#
# 결과물: build\windows\ 아래 실행파일. (개발 중 빠른 실행은 'uv run naver-blog-crawler-gui')
#
# 참고: Flutter/VS 툴체인 설치가 부담되면 flet build 대신 PyInstaller 기반
#       'uv run flet pack src\naver_blog_crawler\gui.py' 로 단일 exe를 만들 수 있다.
#       (백신 오탐·큰 용량 등 단점이 있어 배포보다 임시 실행용 폴백으로 권장.)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

if (-not (Test-Command "uv")) {
    throw "uv가 설치되어 있지 않습니다. https://docs.astral.sh/uv/ 를 참고하세요."
}

# Visual Studio C++ 빌드 도구(VC.Tools 워크로드)를 확인하고 없으면 설치한다.
$vsComponentId = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
$vsInstallerDir = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer"
$vswhere = Join-Path $vsInstallerDir "vswhere.exe"
$vsSetup = Join-Path $vsInstallerDir "vs_installer.exe"

function Test-VCTools {
    if (-not (Test-Path $vswhere)) {
        return $false
    }
    $found = & $vswhere -products * -requires $vsComponentId -property installationPath 2>$null
    return [bool]$found
}

function Get-VSInstallPath {
    # VC.Tools 보유 여부와 무관하게, 설치된 VS 인스턴스 경로를 반환한다(없으면 $null).
    if (-not (Test-Path $vswhere)) {
        return $null
    }
    return (& $vswhere -products * -property installationPath 2>$null | Select-Object -First 1)
}

function Install-VCTools {
    # vs_installer.exe가 있으면 그것으로 직접 설치/수정한다. winget은 취소된 부분 설치를
    # '설치됨'으로 오인해 워크로드 추가를 건너뛰는 한계가 있어 우선하지 않는다.
    $commonArgs = @(
        "--add", $vsComponentId, "--includeRecommended",
        "--passive", "--norestart", "--wait"
    )

    if (Test-Path $vsSetup) {
        $installPath = Get-VSInstallPath
        if ($installPath) {
            Write-Host "==> 기존 Visual Studio에 C++ 워크로드를 추가합니다 (vs_installer modify)."
            $verbArgs = @("modify", "--installPath", $installPath)
        } else {
            Write-Host "==> Visual Studio Build Tools를 설치합니다 (vs_installer install)."
            $verbArgs = @(
                "install",
                "--channelId", "VisualStudio.17.Release",
                "--productId", "Microsoft.VisualStudio.Product.BuildTools"
            )
        }
        Write-Host "    설치 창이 뜨며 수 분~수십 분 걸릴 수 있습니다. 끝까지 기다려 주세요."
        & $vsSetup @verbArgs @commonArgs
    } elseif (Test-Command "winget") {
        Write-Host "==> winget으로 Visual Studio Build Tools를 설치합니다. 수 분~수십 분 걸릴 수 있습니다."
        $override = "--add $vsComponentId --includeRecommended --passive --norestart --wait"
        winget install --id Microsoft.VisualStudio.2022.BuildTools `
            --accept-package-agreements --accept-source-agreements `
            --override $override
    } else {
        throw @"
Visual Studio Build Tools를 자동 설치할 수단(vs_installer/winget)이 없습니다.
'Desktop development with C++' 워크로드를 수동 설치하세요:
  https://visualstudio.microsoft.com/downloads/
"@
    }
}

if (Test-VCTools) {
    Write-Host "==> Visual Studio C++ 빌드 도구 확인됨"
} else {
    Write-Host "==> Visual Studio C++ 빌드 도구가 없습니다. 설치를 진행합니다 (UAC 권한 요청이 뜰 수 있음)."
    Install-VCTools
    # vs_installer는 --wait를 줘도 백그라운드로 이어 설치하는 경우가 있어, 성공 판정은 재확인으로 한다.
    if (-not (Test-VCTools)) {
        throw @"
C++ 워크로드 설치를 확인하지 못했습니다.
설치 관리자가 백그라운드에서 계속 진행 중일 수 있습니다.
Visual Studio Installer 창에서 설치 완료를 확인한 뒤 이 스크립트를 다시 실행하세요.
"@
    }
    Write-Host "==> Visual Studio C++ 빌드 도구 설치 완료"
}

Write-Host "==> 의존성 동기화 (gui 포함)"
uv sync --extra gui
if ($LASTEXITCODE -ne 0) {
    throw "uv sync 실패 (exit code $LASTEXITCODE)."
}

Write-Host "==> flet build windows"
uv run flet build windows `
    --product "Naver Blog Backup" `
    --org com.thsvkd
if ($LASTEXITCODE -ne 0) {
    throw "flet build windows 실패 (exit code $LASTEXITCODE)."
}

# flet이 에러를 내고도 0으로 끝나는 경우가 있어 결과물 존재를 직접 확인한다.
$exe = Get-ChildItem -Path "build\windows" -Filter *.exe -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $exe) {
    throw "빌드가 끝났지만 build\windows 에서 .exe를 찾지 못했습니다."
}

Write-Host "==> 완료: $($exe.FullName)"
