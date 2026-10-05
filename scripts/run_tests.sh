#!/usr/bin/env bash
# 테스트 실행 (Gemini 호출 없음, R6).
#
# PYTHONPATH 를 비우고 실행한다. 이 개발 PC 에는 ROS 가 python3.10 경로를 PYTHONPATH 로
# 주입하는데, 그 안의 pytest 플러그인이 python3.11 환경에서 로드되면 수집 단계에서 깨진다.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=
export SVMTRIAL_BACKEND=offline
exec conda run --no-capture-output -n Valeo_SVM_Trial python -m pytest "$@"
