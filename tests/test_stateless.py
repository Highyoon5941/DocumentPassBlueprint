"""학습 없음(무상태) 보증 — 이 파이프라인은 지속되는 모델을 만들지 않는다.

주장: "어떤 형식의 문서든 Pass/Fail 라벨과 함께 주면, 그 배치에서만 적합해 가이드라인을 낸다."
그 주장을 깨뜨리는 변경(모델 직렬화, 문서종류 하드코딩, 배치 간 상태 공유)을 막는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "svmtrial"


def _xy(n=60, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.integers(0, 2, (n, 5)).astype(float), columns=[f"c{i}" for i in range(5)])
    X.index = [f"D{i:03d}" for i in range(n)]
    X.index.name = "doc_id"
    y = pd.Series(((X.c0 + X.c3) >= 1).astype(float).to_numpy(), index=X.index)
    return X, y


# ---------------------------------------------------------------- 모델 비지속


def test_no_model_serialization_in_source():
    """학습된 모델을 디스크에 쓰는 코드가 없어야 한다 (pickle/joblib/torch.save)."""
    bad = []
    for f in sorted(SRC.glob("*.py")):
        txt = f.read_text(encoding="utf-8")
        for pat in (r"\bimport pickle\b", r"\bimport joblib\b", r"\bjoblib\.(dump|load)\b",
                    r"\btorch\.save\b", r"\bpickle\.(dump|dumps|load)\b"):
            if re.search(pat, txt):
                bad.append(f"{f.name}: {pat}")
    assert not bad, f"모델 직렬화가 도입됐다 — 무상태 보증이 깨진다: {bad}"


def test_modeling_does_not_import_llm():
    """계수를 만드는 층은 LLM 과 무관해야 한다 (역할 분리)."""
    for name in ("modeling.py", "features.py", "counterfactual.py", "labels.py", "report.py"):
        txt = (SRC / name).read_text(encoding="utf-8")
        assert "gemini_client" not in txt, f"{name} 이 LLM 진입점을 import 한다"
        assert "generate_json" not in txt, f"{name} 이 LLM 을 호출한다"


def test_fit_is_deterministic(settings):
    """같은 입력이면 같은 계수 — 재현성(R9). 숨은 상태가 있으면 깨진다."""
    from svmtrial.modeling import fit_svm_weights

    X, y = _xy()
    a = fit_svm_weights(settings, X, y, "svm_C0.1")
    b = fit_svm_weights(settings, X, y, "svm_C0.1")
    assert a["weights"] == b["weights"]
    assert a["intercept"] == b["intercept"]


def test_fit_depends_only_on_given_batch(settings):
    """다른 배치는 다른 계수를 준다 — 이전 배치가 남아 영향을 주지 않는다."""
    from svmtrial.modeling import fit_svm_weights

    X1, y1 = _xy(seed=0)
    X2, y2 = _xy(seed=99)
    w1 = fit_svm_weights(settings, X1, y1, "svm_C0.1")["weights"]
    w2 = fit_svm_weights(settings, X2, y2, "svm_C0.1")["weights"]
    assert w1 != w2, "배치가 달라도 계수가 같다 — 전역 상태 의심"
    # 그리고 1번 배치를 다시 적합하면 원래 값으로 돌아와야 한다(2번의 흔적이 없다)
    again = fit_svm_weights(settings, X1, y1, "svm_C0.1")["weights"]
    assert again == w1, "앞선 적합이 다음 적합에 영향을 준다"


# ---------------------------------------------------------------- 문서종류 불변


def test_seed_absent_for_unknown_doc_type():
    """seeds.py 에 없는 문서종류는 빈 seed → 분류체계를 데이터에서 만든다."""
    from svmtrial.seeds import seed_for

    assert seed_for("대책서"), "대책서 seed 가 사라졌다"
    assert seed_for("요구사양서") == []
    assert seed_for("한 번도 본 적 없는 문서종류") == []


def test_empty_seed_does_not_fall_back_to_8d(settings):
    """빈 seed 가 대책서의 8D 로 되돌아가면, 새 문서종류가 엉뚱한 섹션을 물려받는다 (회귀 방지)."""
    from svmtrial import parts as P
    from svmtrial.offline_backend import OfflineBackend
    from svmtrial.payload import wrap
    from svmtrial.schemas import SectionTaxonomy

    titles = ["R1 Scope", "R3 Functional requirements", "R7 Verification"]
    payload, _ = OfflineBackend(settings).generate(
        model="m", prompt_id="section_taxonomy.v1",
        parts=[P.text(wrap({"titles": titles, "seed": [], "n_docs": 10,
                            "title_doc_counts": {t: 10 for t in titles}}))],
        schema=SectionTaxonomy,
    )
    ids = {i["id"] for i in payload["items"]}
    assert not (ids & {"D0", "D1", "D2", "D4", "D7", "D8"}), f"8D 로 되돌아갔다: {sorted(ids)}"
    assert len(payload["items"]) == len(titles), f"관측 제목 수와 다르다: {payload['items']}"


def test_title_doc_count_threshold_uses_doc_counts(settings):
    """제목 목록은 중복 제거돼 있으므로, 문서 수 임계값은 title_doc_counts 로 판단해야 한다."""
    from svmtrial import parts as P
    from svmtrial.offline_backend import OfflineBackend
    from svmtrial.payload import wrap
    from svmtrial.schemas import SectionTaxonomy

    titles = ["R1 Scope", "R3 Functional requirements"]
    b = OfflineBackend(settings)
    # 문서 수가 충분하면 항목이 만들어진다
    many, _ = b.generate(model="m", prompt_id="section_taxonomy.v1",
                         parts=[P.text(wrap({"titles": titles, "seed": [], "n_docs": 10,
                                             "title_doc_counts": {t: 10 for t in titles}}))],
                         schema=SectionTaxonomy)
    assert len(many["items"]) == 2
    # 한 문서에만 나온 제목은 섹션으로 올리지 않는다
    few, _ = b.generate(model="m", prompt_id="section_taxonomy.v1",
                        parts=[P.text(wrap({"titles": titles, "seed": [], "n_docs": 10,
                                            "title_doc_counts": {t: 1 for t in titles}}))],
                        schema=SectionTaxonomy)
    assert few["items"] == []


def test_generalized_heading_pattern_matches_non_8d():
    """제목 번호 패턴이 대책서(D*)에만 묶이면 다른 문서종류의 제목이 하나도 안 잡힌다 (회귀 방지)."""
    from svmtrial.offline_backend import _HEAD_HINT

    for t in ("D4 Root cause", "R1 Scope and applicability", "R7 Verification and test",
              "1. 문제 정의", "1.2 세부항목", "제3장 적용범위", "III. Appendix"):
        assert _HEAD_HINT.match(t), f"제목으로 인식되지 않았다: {t!r}"
    for t in ("  requirement text placeholder ", "Acceptance criteria table: Item | Spec",
              "5-Why analysis table: Why1"):
        assert not _HEAD_HINT.match(t), f"본문이 제목으로 잡혔다: {t!r}"
