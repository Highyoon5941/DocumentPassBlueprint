"""Claude 연결 점검 — 인증 → 구조화 출력 1회 호출 → 토큰/비용 확인.

사용:
  python scripts/check_claude.py              # 인증 + 테스트 호출 1회
  python scripts/check_claude.py --no-call    # 네트워크 없이 설정·자격증명만 점검
  python scripts/check_claude.py --estimate <그룹>   # 그 그룹 실행 시 호출 수·토큰 추정

`scripts/check_gemini.py` 의 Claude 대응물이다.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-call", action="store_true", help="실제 호출 없이 설정만 점검")
    ap.add_argument("--estimate", metavar="GROUP", help="그룹 실행 시 호출 수·토큰 추정")
    ap.add_argument("--count-tokens", metavar="PNG",
                    help="실제 OCR 1페이지의 입력 토큰을 측정한다 (비용 산정의 기준)")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()

    from svmtrial.config import load_settings

    s = load_settings(ROOT, config_path=a.config)

    print(f"[i] SVMTRIAL_BACKEND = {s.backend}")
    if s.backend != "claude":
        print("[!] 백엔드가 claude 가 아닙니다. .env 에 SVMTRIAL_BACKEND=claude 를 넣으세요.")
        print("    (설정 점검은 계속합니다)")
    print(f"[i] model_fast = {s.claude.model_fast}   (effort={s.claude.effort_fast})")
    print(f"[i] model_pro  = {s.claude.model_pro}   (effort={s.claude.effort_pro})")
    print(f"[i] prompt_cache = {s.claude.prompt_cache} / max_tokens = {s.claude.max_tokens}")

    # --- 자격증명
    key = os.getenv("ANTHROPIC_API_KEY")
    tok = os.getenv("ANTHROPIC_AUTH_TOKEN")
    if key:
        print(f"[OK] ANTHROPIC_API_KEY 설정됨 (…{key[-4:]})")
    elif tok:
        print("[OK] ANTHROPIC_AUTH_TOKEN 설정됨")
    else:
        print("[!] 환경변수가 없습니다. `ant auth login` 프로필이 있으면 그대로 동작합니다.")
        print("    확인: ant auth status")

    # --- 호출 수 추정
    if a.estimate:
        import json
        import math

        from svmtrial.groups import parse_group

        g = parse_group(a.estimate)
        f = s.work / "ingest" / f"{g.safe}.json"
        if not f.exists():
            print(f"[X] {f} 가 없습니다 — 먼저 `python -m svmtrial ingest -g {g.key}` 를 실행하세요.")
            return 1
        ing = json.loads(f.read_text(encoding="utf-8"))
        pages, docs = ing["n_pages"], ing["n_docs"]
        per = s.analysis.concepts_per_scoring_call
        for ncon in (10, 30):
            chunks = math.ceil(ncon / per)
            fast = pages + docs * chunks + math.ceil(docs * s.analysis.scoring_recheck_ratio) * chunks + 1
            pro = 1 + s.analysis.discovery.rounds + 1 + 1
            print(f"\n[추정] 개념 {ncon}개 가정 — {g.key} (문서 {docs}건 / 페이지 {pages}장)")
            print(f"   FAST({s.claude.model_fast}) {fast:5d}회")
            print(f"   PRO ({s.claude.model_pro}) {pro:5d}회")
            print(f"   → 정확한 토큰은 `python -m svmtrial ocr -g {g.key} --dry-run` 와")
            print("     첫 실행 후 logs/<run_id>/gemini_calls.jsonl 의 실측값으로 보정하세요.")
        return 0

    # --- 실제 페이지로 입력 토큰 측정 (비용 산정의 기준)
    if a.count_tokens:
        png = Path(a.count_tokens)
        if not png.exists():
            print(f"[X] 파일이 없습니다: {png}")
            return 1
        import base64

        import anthropic

        from svmtrial.gemini_client import load_prompt
        from svmtrial.payload import wrap
        from svmtrial.schemas import PageLayout

        prompt = load_prompt(s, "ocr_page.v1",
                             data_block=wrap({"doc_id": png.parent.name, "page": 1, "n_pages": 1}))
        i = prompt.find("<<<SVMTRIAL_DATA")
        system_text, user_text = (prompt[:i].rstrip(), prompt[i:]) if i > 0 else (None, prompt)
        client = anthropic.Anthropic()
        kw = {
            "model": s.claude.model_fast,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.standard_b64encode(png.read_bytes()).decode()}},
            ]}],
            "output_format": PageLayout,
            "thinking": {"type": "adaptive"},
        }
        if system_text:
            kw["system"] = [{"type": "text", "text": system_text}]
        n = client.messages.count_tokens(**kw).input_tokens
        sys_only = client.messages.count_tokens(
            model=s.claude.model_fast, system=[{"type": "text", "text": system_text or ""}],
            messages=[{"role": "user", "content": "."}]).input_tokens if system_text else 0
        print(f"\n[측정] {png}  ({png.stat().st_size // 1024} KB)")
        print(f"   OCR 1페이지 입력 토큰 = {n:,}")
        print(f"   그중 고정 지시문(system, 캐시 대상) ≈ {sys_only:,}")
        print(f"   → 캐시 적중 시 2회차부터 약 {sys_only:,} 토큰이 1/10 단가로 청구됩니다")
        print(f"\n   568페이지 기준 입력 토큰 ≈ {n * 568:,}")
        print("   비용 = (입력 토큰/1e6 × 입력단가) + (출력 토큰/1e6 × 출력단가)")
        print(f"   {s.claude.model_fast} 단가는 docs/claude_backend_guide.md §6 표를 보세요")
        return 0

    if a.no_call:
        print("\n[i] --no-call: 실제 호출은 생략했습니다.")
        return 0

    # --- 실제 호출 1회
    if s.backend != "claude":
        print("\n[X] 실제 호출은 SVMTRIAL_BACKEND=claude 일 때만 합니다.")
        return 1
    try:
        from svmtrial.claude_backend import ClaudeBackend

        out = ClaudeBackend(s).ping()
    except Exception as e:  # noqa: BLE001 - 어떤 실패든 같은 안내를 준다
        print(f"\n[X] 호출 실패: {type(e).__name__}: {e}")
        print("  점검 항목:")
        print("   1) ANTHROPIC_API_KEY 가 유효한가 (또는 `ant auth status`)")
        print("   2) 네트워크/프록시 — 사내망이면 HTTPS_PROXY, SSL_CERT_FILE")
        print("   3) 모델 ID 가 맞는가 (config 의 claude.model_fast)")
        return 1

    print("\n[OK] 구조화 출력 응답:", out["parsed"])
    print(f"[OK] 모델 {out['model']} / stop_reason={out['stop_reason']}")
    print(f"[OK] 토큰: 입력 {out['input_tokens']} / 출력 {out['output_tokens']}")
    print("\nCLAUDE 연결 정상 — `python -m svmtrial all -g <그룹>` 으로 진행할 수 있습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
