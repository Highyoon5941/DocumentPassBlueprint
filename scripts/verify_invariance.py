"""문서종류 불변성 검증 — 학습된 모델 없이, 주어진 문서 배치에서 숨김 규칙을 다시 찾는가.

`verify_dummy.py` 가 대책서 전용인 것과 달리, 이 스크립트는 **숨김 규칙 사양을 인자로 받아**
어떤 문서종류에도 쓸 수 있다. 사양은 JSON 파일이나 내장 프리셋으로 준다.

사용:
  python scripts/verify_invariance.py -g 요구사양서__CUST_R --preset reqspec --config <cfg>
  python scripts/verify_invariance.py -g 대책서__CUST_A    --preset taisakusho --config <cfg>
  python scripts/verify_invariance.py -g <그룹> --spec my_rules.json --config <cfg>

사양 형식 (JSON):
  {"accept": {"keywords": ["acceptance criteria"], "weight": 2.0}, ...}
  weight > 0 → `확정`/`유력` 이어야 합격 / weight == 0 → `확정` 이 아니어야 합격

종료 코드 0 = 합격, 1 = 불합격.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PRESETS: dict[str, dict[str, dict]] = {
    # 대책서 (8D) — make_dummy_data.py
    "taisakusho": {
        "5Why 근본원인 표": {"keywords": ["5-why", "5why", "why1"], "weight": 1.0},
        "효과검증 그래프": {"keywords": ["trend graph", "defect rate", "ppm"], "weight": 1.0},
        "수평전개 표": {"keywords": ["horizontal deployment", "applied date"], "weight": 1.0},
        "현물 사진(미끼)": {"keywords": ["close-up", "photo"], "weight": 0.0},
        "팀 명단 표(미끼)": {"keywords": ["team member"], "weight": 0.0},
    },
    # 요구사양서 (R1~R8) — make_dummy_reqspec.py
    "reqspec": {
        "합격판정기준 표": {"keywords": ["acceptance criteria"], "weight": 2.0},
        "추적성 표": {"keywords": ["traceability"], "weight": 1.3},
        "규격 상·하한 표": {"keywords": ["tolerance"], "weight": 0.9},
        "용어정의 표(미끼)": {"keywords": ["glossary"], "weight": 0.0},
        "개정이력 표(미끼)": {"keywords": ["revision history"], "weight": 0.0},
    },
}
GOOD = ("확정", "유력")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", "-g", required=True)
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--spec", help="숨김 규칙 사양 JSON 경로")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    if not (a.preset or a.spec):
        ap.error("--preset 또는 --spec 중 하나가 필요합니다")
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8")) if a.spec else PRESETS[a.preset]

    from svmtrial.config import load_settings
    from svmtrial.groups import parse_group, work_dir
    from svmtrial.modeling import load_results

    s = load_settings(ROOT, config_path=a.config)
    g = parse_group(a.group)
    res = load_results(s, g)
    appr = work_dir(s, "concepts", g) / "concepts_approved.yaml"
    concepts = (yaml.safe_load(appr.read_text(encoding="utf-8")) or {}).get("concepts", [])
    q = {c["id"]: c["question"] for c in concepts}
    verd = {r["feature"]: r for r in res["verdicts"]}
    mv = res["multivariate"]

    print(f"그룹 {g.key}  (문서종류 = {g.doc_type})")
    print(f"  타깃 {res['target_column']} | 문서 {res['prepare']['n_docs']}건 | 특징 {res['prepare']['n_features']}개")
    print(f"  등급 분포 {res['verdict_counts']}")
    print(f"  순열검정 p = {mv.get('permutation_test', {}).get('p_value')}")
    print(f"  탐색적 표기 = {res['exploratory_only']}")
    print()

    ok = True
    print(f"  {'숨김 요인':24s} {'참 w':>6s} {'개념':>6s} {'등급':>5s} {'RD':>8s}  판정")
    print("  " + "-" * 64)
    for name, d in spec.items():
        keys = [k.lower() for k in d["keywords"]]
        w = float(d["weight"])
        hits = [cid for cid, qq in q.items() if any(k in qq.lower() for k in keys)]
        if not hits:
            mark = "✓ (미끼는 후보에 없어도 정상)" if w == 0 else "✗ 개념 후보에 없음"
            ok &= w == 0
            print(f"  {name:24s} {w:6.1f} {'—':>6s} {'—':>5s} {'—':>8s}  {mark}")
            continue
        for cid in hits:
            r = verd.get(cid, {})
            v = r.get("verdict", "미채점")
            rd = r.get("rd")
            good = (v in GOOD) if w > 0 else (v != "확정")
            ok &= good
            rds = f"{rd:+8.3f}" if isinstance(rd, (int, float)) else f"{'—':>8s}"
            print(f"  {name:24s} {w:6.1f} {cid:>6s} {v:>5s} {rds}  {'✓' if good else '✗'}")

    p = mv.get("permutation_test", {}).get("p_value")
    sig = p is not None and p < 0.05
    ok &= sig
    print()
    print(f"  순열검정 p < 0.05 : {'✓' if sig else '✗'} (p={p})")
    print("=" * 68)
    print(f"문서종류 불변성 검증 [{g.doc_type}]: {'합격' if ok else '불합격'}")
    if not ok:
        print("불합격 원인 후보: 문서 수 부족, 개념 문구 불일치, 채점 불안정, 숨김 효과가 너무 약함")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
