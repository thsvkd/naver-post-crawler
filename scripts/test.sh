#!/usr/bin/env bash
# 린트·포맷 검사·테스트를 일괄 수행한다. (pre-commit hook에서도 사용)
set -euo pipefail

# 저장소 루트로 이동(스크립트 위치 기준).
cd "$(dirname "$0")/.."

echo "==> ruff 린트"
uv run ruff check src tests

echo "==> ruff 포맷 검사"
uv run ruff format --check src tests

echo "==> pytest"
uv run pytest

echo "==> 모든 검사 통과"
