#!/usr/bin/env bash
# 개발 환경을 준비한다. (의존성 설치)
set -euo pipefail

# 저장소 루트로 이동(스크립트 위치 기준).
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "오류: uv가 설치되어 있지 않습니다. https://docs.astral.sh/uv/ 를 참고하세요." >&2
  exit 1
fi

echo "==> 의존성 동기화 (uv sync)"
uv sync

# pre-commit hook 설치: 커밋 전에 포매팅·린팅·테스트를 강제한다.
if [ -d .git ]; then
  echo "==> pre-commit hook 설치"
  cat > .git/hooks/pre-commit <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
exec "$(git rev-parse --show-toplevel)/scripts/test.sh"
HOOK
  chmod +x .git/hooks/pre-commit
fi

echo "==> 완료. 'scripts/run.sh <BLOG_ID>' 로 실행할 수 있습니다."
