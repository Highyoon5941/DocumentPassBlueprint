"""config.yaml + .env 로드 (SETUP.md §14).

`${VAR}` 형태의 값은 환경변수로 치환한다. 치환 결과가 비면 None 이 된다.
"""

from __future__ import annotations

import os
import re
import sys
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

_ENV_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def check_interpreter_hygiene() -> list[str]:
    """다른 파이썬 버전의 site-packages가 sys.path에 섞여 있는지 확인한다 (R9 재현성).

    이 개발 PC에서는 ROS가 PYTHONPATH로 python3.10 경로를 주입한다. 3.11 인터프리터에서
    3.10 패키지가 import되면 재현 불가능한 오류가 난다. 발견하면 경고 문자열을 돌려준다.
    """
    mine = f"python{sys.version_info.major}.{sys.version_info.minor}"
    bad = [p for p in sys.path if re.search(r"python3\.\d+", p) and mine not in p]
    if bad:
        return [
            "다른 파이썬 버전의 경로가 sys.path에 있습니다 (PYTHONPATH 오염): " + ", ".join(bad),
            f"조치: 실행 전에 `unset PYTHONPATH` (Windows: `set PYTHONPATH=`) 하거나 {mine} 전용 셸을 쓰세요.",
        ]
    return []


# ---------------------------------------------------------------- 설정 모델


class PathsCfg(BaseModel):
    raw_pdfs: str = "data/raw/pdfs"
    labels: str = "data/raw/labels"
    work: str = "work"
    outputs: str = "outputs"


class LabelsCfg(BaseModel):
    columns: dict[str, str] = {}
    pass_values: list[str] = []
    fail_values: list[str] = []


class IngestCfg(BaseModel):
    dpi: int = 150
    landscape_ratio: float = 1.25
    retry_dpi: int = 250


class GeminiCfg(BaseModel):
    model_fast: str | None = None
    model_pro: str | None = None
    max_workers: int = 4
    max_retries: int = 6
    media_resolution_ocr: str = "HIGH"
    cost_per_1m_tokens: dict[str, float] = {}
    est_tokens_per_page: int = 1800


class OcrCfg(BaseModel):
    min_legibility: float = 0.5


class SplitCfg(BaseModel):
    discovery: float = 0.6
    seed: int = 42


class DiscoveryCfg(BaseModel):
    rounds: int = 5
    k_per_class: int = 4
    max_hypotheses: int = 15


class CvCfg(BaseModel):
    n_splits: int = 5
    n_repeats: int = 10


class VerdictBand(BaseModel):
    q: float
    stability: float


class VerdictCfg(BaseModel):
    confirmed: VerdictBand = VerdictBand(q=0.1, stability=0.90)
    likely: VerdictBand = VerdictBand(q=0.25, stability=0.80)
    reference_rd: float = 0.15


class UnanimousGuard(BaseModel):
    low: float = 0.15
    high: float = 0.85


class AnalysisCfg(BaseModel):
    target: str = "unanimous"
    pool_customers: bool = False
    split: SplitCfg = SplitCfg()
    discovery: DiscoveryCfg = DiscoveryCfg()
    concepts_per_scoring_call: int = 25
    min_rater_labels: int = 30
    cv: CvCfg = CvCfg()
    n_permutations: int = 500
    n_bootstrap: int = 200
    scoring_recheck_ratio: float = 0.1
    scoring_min_agreement: float = 0.85
    min_n_per_feature: int = 10
    unanimous_guard: UnanimousGuard = UnanimousGuard()
    verdict: VerdictCfg = VerdictCfg()


class TemplateCfg(BaseModel):
    section_min_presence: float = 0.7
    font: str = "맑은 고딕"
    pptx_size: str = "16x9"


class Settings(BaseModel):
    root: Path
    backend: str = "offline"
    project: str | None = None
    location: str = "global"
    paths: PathsCfg = PathsCfg()
    labels: LabelsCfg = LabelsCfg()
    ingest: IngestCfg = IngestCfg()
    gemini: GeminiCfg = GeminiCfg()
    ocr: OcrCfg = OcrCfg()
    analysis: AnalysisCfg = AnalysisCfg()
    template: TemplateCfg = TemplateCfg()
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    # --- 경로 헬퍼 ---
    def p(self, which: str, *parts: str) -> Path:
        base = self.root / getattr(self.paths, which)
        return base.joinpath(*parts) if parts else base

    @property
    def work(self) -> Path:
        return self.p("work")

    @property
    def outputs(self) -> Path:
        return self.p("outputs")

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def prompts_dir(self) -> Path:
        return self.root / "prompts"

    def model_for(self, tier: str) -> str:
        """tier: 'fast' | 'pro'. offline 백엔드에서는 ID가 없어도 된다."""
        name = self.gemini.model_fast if tier == "fast" else self.gemini.model_pro
        if not name:
            if self.backend == "offline":
                return f"offline-{tier}"
            raise RuntimeError(
                f".env 의 GEMINI_MODEL_{tier.upper()} 가 비어 있습니다. "
                "`python scripts/check_gemini.py --list-only` 로 사용 가능한 모델 ID를 확인해 기입하세요."
            )
        return name


def _subst_env(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _subst_env(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_subst_env(v) for v in node]
    if isinstance(node, str):
        m = _ENV_RE.match(node.strip())
        if m:
            return os.getenv(m.group(1)) or None
    return node


def load_settings(root: Path | str | None = None, config_path: Path | str | None = None) -> Settings:
    root = Path(root).resolve() if root else PROJECT_ROOT
    load_dotenv(root / ".env")
    cfg_file = Path(config_path) if config_path else root / "config" / "config.yaml"
    raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
    raw = _subst_env(raw or {})

    backend = (os.getenv("SVMTRIAL_BACKEND") or "offline").strip().lower()
    if backend not in {"offline", "vertex"}:
        raise RuntimeError(f"SVMTRIAL_BACKEND 값이 잘못됐습니다: {backend!r} (offline | vertex)")

    def sec(name: str) -> dict:
        v = raw.get(name) or {}
        return {k: v for k, v in v.items() if v is not None} if isinstance(v, dict) else {}

    s = Settings(
        root=root,
        backend=backend,
        project=os.getenv("GOOGLE_CLOUD_PROJECT") or None,
        location=os.getenv("GOOGLE_CLOUD_LOCATION") or "global",
        paths=PathsCfg(**sec("paths")),
        labels=LabelsCfg(**sec("labels")),
        ingest=IngestCfg(**sec("ingest")),
        gemini=GeminiCfg(**sec("gemini")),
        ocr=OcrCfg(**sec("ocr")),
        analysis=AnalysisCfg(**sec("analysis")),
        template=TemplateCfg(**sec("template")),
        raw=raw,
    )
    for msg in check_interpreter_hygiene():
        warnings.warn(msg, RuntimeWarning, stacklevel=2)
    return s


@lru_cache(maxsize=1)
def settings() -> Settings:
    return load_settings()
