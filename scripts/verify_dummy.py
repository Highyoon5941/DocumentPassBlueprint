"""더미 데이터의 숨김 정답을 파이프라인이 다시 찾아냈는지 검증한다 (SETUP.md §12 Phase 6).

합격 기준:
  1. 숨김 규칙 3개(5Why / 효과검증 그래프 / 수평전개 표)에 대응하는 개념이 `확정` 또는 `유력`
  2. 순열검정 p < 0.05
  3. 잡음 요소(사진 / 팀 명단 표)는 `확정` 이 아님

사용: python scripts/verify_dummy.py --group 대책서__CUST_A [--truth data/dummy/truth.csv]
종료 코드 0 = 합격, 1 = 불합격.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# 숨김 정답 → 개념 질문에서 찾아야 할 핵심 단어
HIDDEN_KEYS = {
    "why5": ["5-why", "5why", "why1"],
    "graph": ["trend graph", "defect rate", "ppm", "그래프"],
    "yokoten": ["horizontal deployment", "수평전개", "applied date"],
}
NOISE_KEYS = {
    "photo": ["close-up", "photo", "사진"],
    "org": ["team member", "팀 명단", "dept"],
}
GOOD = ("확정", "유력")


def _match(question: str, keys: list[str]) -> bool:
    q = question.lower()
    return any(k.lower() in q for k in keys)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", "-g", required=True)
    ap.add_argument("--config", default=None)
    a = ap.parse_args()

    from svmtrial.config import load_settings
    from svmtrial.groups import parse_group, work_dir
    from svmtrial.modeling import load_results

    s = load_settings(ROOT, config_path=a.config)
    g = parse_group(a.group)
    res = load_results(s, g)

    appr = work_dir(s, "concepts", g) / "concepts_approved.yaml"
    concepts = (yaml.safe_load(appr.read_text(encoding="utf-8")) or {}).get("concepts", [])
    cmap = {c["id"]: c["question"] for c in concepts}
    verd = {r["feature"]: r["verdict"] for r in res["verdicts"]}
    basis = {r["feature"]: r.get("verdict_basis", "") for r in res["verdicts"]}

    print(f"그룹 {g.key} | 타깃 {res['target_column']} | 문서 {res['prepare']['n_docs']}건 "
          f"| 특징 {res['prepare']['n_features']}개")
    print(f"등급 분포: {res['verdict_counts']}")
    perm = res["multivariate"].get("permutation_test", {})
    print(f"순열검정 p = {perm.get('p_value')}  (기준 < 0.05)")
    print()

    ok = True

    print("[1] 숨김 규칙 3개가 확정/유력 인가")
    for name, keys in HIDDEN_KEYS.items():
        hits = [(cid, verd.get(cid, "미채점")) for cid, q in cmap.items() if _match(q, keys)]
        if not hits:
            print(f"    ✗ {name:8s} → 대응 개념을 찾지 못함 (개념 후보에 없음)")
            ok = False
            continue
        best = max(hits, key=lambda h: GOOD.index(h[1]) if h[1] in GOOD else 9)
        passed = any(v in GOOD for _, v in hits)
        mark = "✓" if passed else "✗"
        ok &= passed
        for cid, v in hits:
            print(f"    {mark} {name:8s} → {cid} = {v:4s}  | {basis.get(cid, '')[:62]}")
        del best

    print()
    print("[2] 순열검정 p < 0.05")
    p = perm.get("p_value")
    if p is not None and p < 0.05:
        print(f"    ✓ p = {p}")
    else:
        print(f"    ✗ p = {p}")
        ok = False

    print()
    print("[3] 잡음 요소가 '확정' 이 아닌가")
    for name, keys in NOISE_KEYS.items():
        hits = [(cid, verd.get(cid, "미채점")) for cid, q in cmap.items() if _match(q, keys)]
        if not hits:
            print(f"    · {name:6s} → 개념 후보에 없음 (문제 없음)")
            continue
        for cid, v in hits:
            bad = v == "확정"
            print(f"    {'✗' if bad else '✓'} {name:6s} → {cid} = {v}")
            if bad:
                ok = False

    print()
    print("=" * 56)
    print("Phase 6 검증: " + ("합격" if ok else "불합격"))
    if not ok:
        print("불합격 원인 후보: 문서 수 부족(FDR 보정이 강함), 개념 문구 불일치, 채점 불안정")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
