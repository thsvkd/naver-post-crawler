#!/usr/bin/env bash
# 크롤러를 실행한다. 인자는 그대로 CLI로 전달된다.
#
# 사용 예:
#   scripts/run.sh winter9377
#   scripts/run.sh winter9377 --limit 10 -o output
set -euo pipefail

# 저장소 루트로 이동(스크립트 위치 기준).
cd "$(dirname "$0")/.."

if [ "$#" -eq 0 ]; then
  echo "사용법: scripts/run.sh <BLOG_ID> [옵션]" >&2
  echo "옵션은 'uv run naver-blog-crawler --help' 참고." >&2
  exit 1
fi

uv run naver-blog-crawler "$@"
