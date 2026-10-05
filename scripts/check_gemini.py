"""Vertex AI(Gemini) 연결 점검: 인증 → 모델 목록 → 이미지+구조화 출력 1회 호출.

사용: python scripts/check_gemini.py            (모델 목록 + 테스트 호출)
      python scripts/check_gemini.py --list-only
      python scripts/check_gemini.py --offline   (백엔드 배선만 점검, 네트워크 없이)

SETUP.md §13.2 기준. `.env` 의 SVMTRIAL_BACKEND 가 offline 이면 자동으로 --offline 처럼 동작한다.
"""

import argparse
import io
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

load_dotenv(ROOT / ".env")
PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
MODEL = os.getenv("GEMINI_MODEL_FAST")
BACKEND = (os.getenv("SVMTRIAL_BACKEND") or "offline").strip().lower()


class Probe(BaseModel):
    text_seen: str
    is_slide_like: bool


def _probe_png() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1600, 900), "white")
    ImageDraw.Draw(img).text((100, 400), "D4 Root cause - 5 Why", fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def check_offline() -> int:
    """네트워크 없이 백엔드 배선과 스키마만 점검한다."""
    from svmtrial import parts as P
    from svmtrial.config import load_settings
    from svmtrial.gemini_client import GeminiClient
    from svmtrial.offline_backend import OfflineBackend
    from svmtrial.payload import wrap
    from svmtrial.schemas import DocFormatGuess

    s = load_settings(ROOT)
    print(f"[i] backend = {s.backend} (offline 점검 모드)")
    fx = OfflineBackend.fixture_path(s)
    print(f"[{'OK' if fx.exists() else '!'}] offline fixture: {fx} "
          f"({'있음' if fx.exists() else '없음 — scripts/make_dummy_data.py 먼저 실행'})")

    gc = GeminiClient(s, run_id="check")
    out = gc.generate_json(
        model=s.model_for("fast"), prompt_id="doc_format.v1",
        parts=[P.text("판정하라.\n" + wrap({"ratios": [1.78, 1.78]})), P.image(_probe_png())],
        schema=DocFormatGuess, cache_key_extra="check_gemini",
    )
    print(f"[OK] 구조화 응답: {out}")
    print("[OK] 로그: logs/check/gemini_calls.jsonl")
    print("\n백엔드 배선 정상. 단, offline 은 실제 Gemini 를 호출하지 않는다.")
    print("실업무 전환 절차는 docs/migration_vertexAI.md 를 보라.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-only", action="store_true", help="모델 목록만 출력")
    ap.add_argument("--offline", action="store_true", help="네트워크 없이 백엔드 배선만 점검")
    a = ap.parse_args()

    if a.offline or BACKEND == "offline":
        if not a.offline:
            print("[i] .env 의 SVMTRIAL_BACKEND=offline 이므로 offline 점검으로 진행합니다.")
            print("    실제 Vertex AI 를 점검하려면 .env 에서 SVMTRIAL_BACKEND=vertex 로 바꾸세요.\n")
        return check_offline()

    if not PROJECT or PROJECT == "your-gcp-project-id":
        sys.exit("[X] .env 에 GOOGLE_CLOUD_PROJECT 를 실제 프로젝트 ID 로 넣으세요.")

    import google.auth

    try:
        creds, detected = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        print(f"[OK] 인증 정보 발견 ({type(creds).__name__}), ADC 프로젝트={detected}")
    except Exception as e:  # noqa: BLE001 - 어떤 인증 오류든 같은 안내를 준다
        sys.exit(f"[X] 인증 실패: {e}\n  → 'gcloud auth application-default login' 실행 또는 "
                 "GOOGLE_APPLICATION_CREDENTIALS(서비스계정 JSON 경로) 설정")

    from google import genai
    from google.genai import types

    # vertexai= 는 enterprise= 와 동일(구 명칭)
    client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    try:
        names = sorted({m.name.split("/")[-1]
                        for m in client.models.list(config={"query_base": True, "page_size": 200})
                        if m.name and "gemini" in m.name})
        print(f"[OK] 사용 가능한 Gemini 모델 ({PROJECT}/{LOCATION}):")
        for n in names:
            print("   -", n)
    except Exception as e:  # noqa: BLE001
        print(f"[!] 모델 목록 조회 실패(권한 부족일 수 있음, 호출은 될 수도 있음): {e}")
    if a.list_only:
        return 0
    if not MODEL:
        sys.exit("[X] .env 에 GEMINI_MODEL_FAST 를 위 목록 중 하나로 넣고 다시 실행하세요.")

    resp = client.models.generate_content(
        model=MODEL,
        contents=[types.Part.from_bytes(data=_probe_png(), mime_type="image/png"),
                  "이미지에 보이는 글자와, 슬라이드(가로형) 형태인지 판단해 JSON으로 답하라."],
        config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                           response_schema=Probe),
    )
    print("[OK] 응답:", resp.parsed)
    u = resp.usage_metadata
    print(f"[OK] 토큰: 입력 {u.prompt_token_count} / 출력 {u.candidates_token_count}")
    print("\nGEMINI 연결 정상 — Gemini CLI 없이 Python SDK로 동작합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
