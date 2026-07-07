"""버전 비교·에셋 선택·릴리스 파싱·다운로드 검증·압축 해제·사이드카 생성 테스트.

실제 네트워크 대신 httpx.MockTransport로 GitHub API/에셋 다운로드 응답을 흉내 낸다.
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

import naver_post_crawler.updater as updater


def test_parse_version_strips_leading_v() -> None:
    # covers: Test-1
    assert updater.parse_version("v1.2.3") == (1, 2, 3)


def test_parse_version_ignores_prerelease_suffix() -> None:
    # covers: Test-2
    assert updater.parse_version("1.2.3-rc1") == (1, 2, 3)
    # prerelease는 최종 릴리스보다 낮게 취급돼야 한다(숫자 코어만 비교하지 않으면
    # (1,2,3,1)이 되어 최종 (1,2,3)보다 크게 잘못 정렬된다).
    assert updater.is_newer("1.2.3-rc1", "1.2.3") is False


def test_is_newer_compares_versions() -> None:
    # covers: Test-3
    assert updater.is_newer("v0.2.0", "0.1.0") is True
    assert updater.is_newer("v0.1.0", "0.1.0") is False
    assert updater.is_newer("v0.0.9", "0.1.0") is False


def test_asset_name_builds_zip_filename() -> None:
    # covers: Test-4
    assert updater.asset_name("windows") == "naver-post-crawler-windows.zip"


def test_current_target_maps_os_names(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-5
    monkeypatch.setattr(updater.platform, "system", lambda: "Windows")
    assert updater.current_target() == "windows"

    monkeypatch.setattr(updater.platform, "system", lambda: "Darwin")
    assert updater.current_target() == "macos"

    monkeypatch.setattr(updater.platform, "system", lambda: "Linux")
    assert updater.current_target() == "linux"


def _release_data(
    tag: str, assets: list[dict[str, object]], body: str = "notes"
) -> dict[str, object]:
    """GitHub `/releases/latest` 응답을 흉내 낸 최소 dict."""
    return {"tag_name": tag, "assets": assets, "body": body}


def _asset(name: str, url: str, digest: str | None = None) -> dict[str, object]:
    asset: dict[str, object] = {"name": name, "browser_download_url": url}
    if digest is not None:
        asset["digest"] = digest
    return asset


def test_parse_latest_returns_none_when_not_newer() -> None:
    # covers: Test-6
    data = _release_data(
        "v0.1.0",
        [_asset("naver-post-crawler-windows.zip", "https://example.com/dl.zip", "sha256:ABC")],
    )
    assert updater.parse_latest(data, "0.1.0", "windows") is None


def test_parse_latest_returns_none_when_no_matching_asset() -> None:
    # covers: Test-7
    data = _release_data(
        "v0.2.0",
        [_asset("naver-post-crawler-macos.zip", "https://example.com/mac.zip", "sha256:ABC")],
    )
    assert updater.parse_latest(data, "0.1.0", "windows") is None


def test_parse_latest_returns_release_when_newer_with_matching_asset() -> None:
    # covers: Test-8
    data = _release_data(
        "v0.2.0",
        [
            _asset(
                "naver-post-crawler-windows.zip",
                "https://example.com/dl/naver-post-crawler-windows.zip",
                "sha256:ABC123",
            )
        ],
    )
    release = updater.parse_latest(data, "0.1.0", "windows")
    assert release is not None
    assert release.tag == "v0.2.0"
    assert release.version == "0.2.0"
    assert release.asset_url == "https://example.com/dl/naver-post-crawler-windows.zip"
    assert release.sha256 == "ABC123"


def test_parse_latest_sha256_none_without_digest() -> None:
    # covers: Test-9
    data = _release_data(
        "v0.2.0",
        [_asset("naver-post-crawler-windows.zip", "https://example.com/dl.zip")],
    )
    release = updater.parse_latest(data, "0.1.0", "windows")
    assert release is not None
    assert release.sha256 is None


def test_sha256_of_matches_known_digest(tmp_path: Path) -> None:
    # covers: Test-10
    payload = b"hello naver-post-crawler"
    file_path = tmp_path / "sample.bin"
    file_path.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert updater.sha256_of(file_path) == expected


def test_download_raises_and_deletes_file_on_sha256_mismatch(tmp_path: Path) -> None:
    # covers: Test-11
    content = b"actual bytes returned by the mocked asset endpoint"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    release = updater.Release(
        tag="v0.2.0",
        version="0.2.0",
        asset_name="naver-post-crawler-windows.zip",
        asset_url="https://example.com/dl/naver-post-crawler-windows.zip",
        sha256="0" * 64,  # content의 실제 sha256과 절대 일치하지 않는 값
        notes="",
    )

    with pytest.raises(RuntimeError) as excinfo:
        updater.download(release, tmp_path, client=client)
    # NotImplementedError는 RuntimeError의 서브클래스라 pytest.raises(RuntimeError)만으로는
    # 아직 구현되지 않은 함수(raise NotImplementedError)를 잘못 통과시킬 수 있다.
    # 정확한 타입까지 확인해 그 함정을 막는다.
    assert type(excinfo.value) is RuntimeError

    # 검증 실패 시 부분 다운로드 파일이 남아있으면 안 된다.
    assert not (tmp_path / release.asset_name).exists()


def test_download_rejects_non_https_before_requesting(tmp_path: Path) -> None:
    # covers: Test-12
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("download()는 non-https asset_url에 대해 요청을 보내면 안 된다")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    release = updater.Release(
        tag="v0.2.0",
        version="0.2.0",
        asset_name="naver-post-crawler-windows.zip",
        asset_url="http://example.com/dl/naver-post-crawler-windows.zip",
        sha256="deadbeef",
        notes="",
    )

    with pytest.raises(RuntimeError) as excinfo:
        updater.download(release, tmp_path, client=client)
    # NotImplementedError는 RuntimeError의 서브클래스이므로 정확한 타입까지 확인한다
    # (위 mismatch 테스트와 같은 이유).
    assert type(excinfo.value) is RuntimeError


def test_extract_returns_single_exe_path(tmp_path: Path) -> None:
    # covers: Test-13
    zip_path = tmp_path / "asset.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("naver-post-crawler.exe", b"dummy exe bytes")

    staging_dir = tmp_path / "staging"
    result = updater.extract(zip_path, staging_dir)

    assert result.exists()
    assert result.is_file()
    assert result.name == "naver-post-crawler.exe"


def test_is_packaged_reflects_frozen_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-14
    assert updater.is_packaged() is False

    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    assert updater.is_packaged() is True


def test_write_sidecar_contains_expected_fields(tmp_path: Path) -> None:
    # covers: Test-15
    install_exe = tmp_path / "install" / "naver-post-crawler.exe"
    new_exe = tmp_path / "staging" / "naver-post-crawler.exe"

    result = updater.write_sidecar(
        tmp_path,
        pid=4321,
        install_exe=install_exe,
        new_exe=new_exe,
        app_exe="naver-post-crawler.exe",
    )

    assert result.exists()
    text = result.read_text(encoding="utf-8")
    assert "4321" in text
    assert str(install_exe) in text
    assert str(new_exe) in text
    assert "naver-post-crawler.exe" in text

    # win32는 .bat, 그 외는 .sh 사이드카를 써야 한다.
    expected_suffix = ".bat" if sys.platform == "win32" else ".sh"
    assert result.suffix == expected_suffix


# -- 유지보수 루프: 하드닝 이후 추가된/변경된 계약 회귀 테스트 --------------------


def test_download_success_writes_verified_bytes(tmp_path: Path) -> None:
    # covers: Test-17
    content = b"real asset bytes served by the mocked endpoint"
    expected_sha = hashlib.sha256(content).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    release = updater.Release(
        tag="v0.2.0",
        version="0.2.0",
        asset_name="naver-post-crawler-windows.zip",
        asset_url="https://example.com/dl/naver-post-crawler-windows.zip",
        sha256=expected_sha,
        notes="",
    )

    result = updater.download(release, tmp_path, client=client)

    assert result == tmp_path / release.asset_name
    assert result.exists()
    assert result.read_bytes() == content


def test_extract_rejects_multiple_top_level_entries(tmp_path: Path) -> None:
    # covers: Test-18
    zip_path = tmp_path / "asset.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("naver-post-crawler.exe", b"exe bytes")
        zf.writestr("readme.txt", b"extra top-level entry")

    with pytest.raises(RuntimeError):
        updater.extract(zip_path, tmp_path / "staging")


def test_extract_renames_entry_to_expected_name(tmp_path: Path) -> None:
    # covers: Test-18
    zip_path = tmp_path / "asset.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("some-other-name.bin", b"exe bytes")

    result = updater.extract(zip_path, tmp_path / "staging", expected_name="naver-post-crawler.exe")

    assert result.name == "naver-post-crawler.exe"
    assert result.exists()
    assert result.is_file()


def test_extract_handles_folder_wrapped_exe(tmp_path: Path) -> None:
    # covers: Test-13 (폴더 배포 zip: naver-post-crawler/naver-post-crawler.exe 구조)
    zip_path = tmp_path / "asset.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("naver-post-crawler/naver-post-crawler.exe", b"dummy exe bytes")

    staging = tmp_path / "staging"
    result = updater.extract(zip_path, staging)

    assert result.exists()
    assert result.is_file()
    assert result.name == "naver-post-crawler.exe"


def test_extract_folder_wrapped_renames_to_expected_name(tmp_path: Path) -> None:
    # covers: Test-13 (폴더 배포 zip + expected_name rename)
    zip_path = tmp_path / "asset.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("naver-post-crawler/some-other-name.bin", b"exe bytes")

    result = updater.extract(zip_path, tmp_path / "staging", expected_name="naver-post-crawler.exe")

    assert result.name == "naver-post-crawler.exe"
    assert result.exists()
    assert result.is_file()


def test_extract_rejects_folder_with_multiple_files(tmp_path: Path) -> None:
    # covers: Test-18 (폴더 안에 파일이 여럿이면 어느 것을 교체 대상으로 할지 모호)
    zip_path = tmp_path / "asset.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("naver-post-crawler/naver-post-crawler.exe", b"exe bytes")
        zf.writestr("naver-post-crawler/readme.txt", b"extra file in folder")

    with pytest.raises(RuntimeError):
        updater.extract(zip_path, tmp_path / "staging")


def _expected_backup(install_exe: Path) -> Path:
    """write_sidecar이 계산하는 단일 파일 백업 경로(<stem>_backup<suffix>)를 재현한다."""
    return install_exe.parent / (install_exe.stem + "_backup" + install_exe.suffix)


def test_write_sidecar_windows_template_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: Test-19
    monkeypatch.setattr(updater.sys, "platform", "win32")
    install_exe = tmp_path / "install" / "naver-post-crawler.exe"
    new_exe = tmp_path / "staging" / "naver-post-crawler.exe"
    backup = _expected_backup(install_exe)

    result = updater.write_sidecar(
        tmp_path,
        pid=4321,
        install_exe=install_exe,
        new_exe=new_exe,
        app_exe="naver-post-crawler.exe",
    )
    text = result.read_text(encoding="utf-8")

    assert str(backup) in text
    assert f'del "{backup}"' in text
    assert "move /Y" in text

    install_to_backup = f'move /Y "{install_exe}" "{backup}"'
    new_to_install = f'move /Y "{new_exe}" "{install_exe}"'
    assert install_to_backup in text
    assert new_to_install in text
    # install→backup 이동이 new→install 이동보다 먼저 실행돼야 한다.
    assert text.index(install_to_backup) < text.index(new_to_install)

    # 성공 종료(exit /b 0)는 롤백/실패 라벨보다 앞에 있어야 한다(먼저 도달하면 그대로 종료).
    exit_ok_idx = text.index("exit /b 0")
    label_positions = [i for i in (text.find(":restore"), text.find(":fail")) if i != -1]
    assert label_positions
    assert exit_ok_idx < min(label_positions)


def test_write_sidecar_posix_template_two_stage_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: Test-19
    monkeypatch.setattr(updater.sys, "platform", "linux")
    install_exe = tmp_path / "install" / "naver-post-crawler"
    new_exe = tmp_path / "staging" / "naver-post-crawler"
    backup = _expected_backup(install_exe)

    result = updater.write_sidecar(
        tmp_path,
        pid=4321,
        install_exe=install_exe,
        new_exe=new_exe,
        app_exe="naver-post-crawler",
    )
    text = result.read_text(encoding="utf-8")

    assert str(backup) in text
    first_mv = f'mv "{install_exe}" "{backup}"'
    restore_rm = f'rm -f "{install_exe}"'
    assert first_mv in text
    assert restore_rm in text
    # install 삭제(롤백)는 반드시 첫 mv(install→backup) 성공 이후 분기에서만 나와야
    # 한다 — 텍스트 상에서도 첫 mv보다 뒤에 있어야 한다(앞에 있으면 무조건 실행되는
    # 코드로 잘못 만들어진 것).
    assert text.index(first_mv) < text.index(restore_rm)


def test_check_latest_uses_expected_url_and_headers_and_returns_release() -> None:
    # covers: Test-20
    expected_url = updater._API_LATEST.format(owner=updater.REPO_OWNER, repo=updater.REPO_NAME)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == expected_url
        assert request.headers.get("User-Agent") == "naver-post-crawler-updater"
        assert request.headers.get("Accept") == "application/vnd.github+json"
        return httpx.Response(
            200,
            json={
                "tag_name": "v0.2.0",
                "assets": [
                    {
                        "name": "naver-post-crawler-windows.zip",
                        "browser_download_url": "https://example.com/dl.zip",
                        "digest": "sha256:ABC123",
                    }
                ],
                "body": "notes",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    release = updater.check_latest(
        "0.1.0", "windows", owner=updater.REPO_OWNER, repo=updater.REPO_NAME, client=client
    )

    assert release is not None
    assert release.version == "0.2.0"


def test_check_latest_returns_none_when_not_newer() -> None:
    # covers: Test-20
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "tag_name": "v0.1.0",
                "assets": [
                    {
                        "name": "naver-post-crawler-windows.zip",
                        "browser_download_url": "https://example.com/dl.zip",
                        "digest": "sha256:ABC123",
                    }
                ],
                "body": "notes",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    release = updater.check_latest("0.1.0", "windows", client=client)

    assert release is None


def test_apply_and_restart_rejects_non_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: Test-21
    monkeypatch.setattr(updater.sys, "platform", "linux")
    with pytest.raises(RuntimeError):
        updater.apply_and_restart("x", install_path=tmp_path / "app.exe")


def test_download_enforces_size_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-22
    monkeypatch.setattr(updater, "MAX_ASSET_BYTES", 10)
    content = b"this payload is definitely more than ten bytes long"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    release = updater.Release(
        tag="v0.2.0",
        version="0.2.0",
        asset_name="naver-post-crawler-windows.zip",
        asset_url="https://example.com/dl/naver-post-crawler-windows.zip",
        sha256=None,  # 크기 상한이 트립되게 하려는 것이지 해시 불일치가 아니다.
        notes="",
    )

    with pytest.raises(RuntimeError):
        updater.download(release, tmp_path, client=client)

    # 상한 초과 시 부분 다운로드 파일이 남아있으면 안 된다.
    assert not (tmp_path / release.asset_name).exists()


def test_check_latest_returns_none_on_404_but_raises_on_500() -> None:
    # covers: Test-23
    def not_found_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = httpx.Client(transport=httpx.MockTransport(not_found_handler))
    # 릴리스가 하나도 없는 저장소는 404를 주는데, 이는 오류가 아니라 "업데이트 없음"이다.
    assert updater.check_latest("0.1.0", "windows", client=client) is None

    def server_error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "Internal Server Error"})

    client = httpx.Client(transport=httpx.MockTransport(server_error_handler))
    # 404만 삼켜지고, 다른 상태 코드(레이트리밋 403, 5xx 등)는 그대로 전파돼야 한다.
    with pytest.raises(httpx.HTTPStatusError):
        updater.check_latest("0.1.0", "windows", client=client)
