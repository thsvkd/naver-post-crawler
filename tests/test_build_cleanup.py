"""build.py의 출력 폴더 선제 정리 헬퍼(_clean_prior_build_output) 회귀 테스트.

flet build는 기존 build/<target>를 shutil.rmtree로 지운 뒤 새 결과물을 복사하는데,
Windows에서 갓 빌드된 실행파일/DLL을 백신·인덱서가 잠깐 잡고 있으면 rmtree가
WinError 145로 죽고 출력물이 깨진 채 남는다. 이를 예방하는 재시도 정리 로직을 검증한다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# scripts/build.py를 고유 모듈명으로 파일 경로에서 직접 로드한다. 그냥 `import build`
# 하면 최상위 이름이 저장소의 build/ 출력 폴더(암시적 네임스페이스 패키지)와 충돌한다.
# (build.py 내부의 `from _common import ...`는 conftest가 scripts/를 sys.path에 올려 해석됨.)
_BUILD_PATH = Path(__file__).parents[1] / "scripts" / "build.py"
_spec = importlib.util.spec_from_file_location("build_script", _BUILD_PATH)
build = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(build)


def test_clean_prior_build_output_noop_when_absent(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 출력 폴더가 아예 없으면 조용히 통과해야 한다(rmtree를 부르지 않는다).
    monkeypatch.setattr(build, "REPO_ROOT", tmp_path)
    called = []
    monkeypatch.setattr(build.shutil, "rmtree", lambda *a, **k: called.append(a))

    build._clean_prior_build_output("windows")

    assert called == []


def test_clean_prior_build_output_removes_existing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 실제 존재하는 출력 폴더는 통째로 지워져야 한다.
    monkeypatch.setattr(build, "REPO_ROOT", tmp_path)
    out_dir = tmp_path / "build" / "windows"
    (out_dir / "Lib").mkdir(parents=True)
    (out_dir / "Lib" / "python312.dll").write_bytes(b"x")

    build._clean_prior_build_output("windows")

    assert not out_dir.exists()


def test_clean_prior_build_output_retries_transient_lock(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 일시적 잠금(OSError)이면 재시도해서 결국 성공해야 한다(첫 실패에 포기 금지).
    monkeypatch.setattr(build, "REPO_ROOT", tmp_path)
    (tmp_path / "build" / "windows").mkdir(parents=True)
    monkeypatch.setattr(build.time, "sleep", lambda *_: None)

    attempts = {"n": 0}

    def flaky_rmtree(_path: object) -> None:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError(145, "디렉터리가 비어 있지 않습니다")

    monkeypatch.setattr(build.shutil, "rmtree", flaky_rmtree)

    build._clean_prior_build_output("windows")  # 예외 없이 반환해야 한다.

    assert attempts["n"] == 3


def test_clean_prior_build_output_fails_when_persistently_locked(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 계속 잠겨 있으면(앱 실행 중 등) 원인을 짚어 명확히 중단(SystemExit)해야 한다.
    # 또한 시도 '사이'에만 대기하고 마지막 시도 뒤에는 대기하지 않아야 한다.
    monkeypatch.setattr(build, "REPO_ROOT", tmp_path)
    (tmp_path / "build" / "windows").mkdir(parents=True)
    sleeps = {"n": 0}
    monkeypatch.setattr(build.time, "sleep", lambda *_: sleeps.__setitem__("n", sleeps["n"] + 1))

    def locked_rmtree(_path: object) -> None:
        raise OSError(145, "디렉터리가 비어 있지 않습니다")

    monkeypatch.setattr(build.shutil, "rmtree", locked_rmtree)

    with pytest.raises(SystemExit):
        build._clean_prior_build_output("windows")

    # N번 시도했다면 대기는 그 사이사이 N-1번뿐(마지막 실패 후 불필요한 대기 금지).
    assert sleeps["n"] == build._CLEAN_RETRIES - 1


def test_clean_prior_build_output_succeeds_if_dir_vanishes_midretry(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 재시도 도중 폴더가 외부(백신 격리 등)에서 통째로 사라지면(FileNotFoundError)
    # 목표(부재)가 이미 달성된 것이므로 오류 없이 반환해야 한다(잠김으로 오판 금지).
    monkeypatch.setattr(build, "REPO_ROOT", tmp_path)
    (tmp_path / "build" / "windows").mkdir(parents=True)
    monkeypatch.setattr(build.time, "sleep", lambda *_: None)

    def vanished_rmtree(_path: object) -> None:
        raise FileNotFoundError(2, "지정된 경로를 찾을 수 없습니다")

    monkeypatch.setattr(build.shutil, "rmtree", vanished_rmtree)

    build._clean_prior_build_output("windows")  # 예외 없이 반환해야 한다.
