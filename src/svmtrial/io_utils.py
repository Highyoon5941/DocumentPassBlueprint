"""표 저장 헬퍼.

SETUP.md §8.3 은 `labels_long.parquet` 를 요구하지만, parquet 쓰기에는 `pyarrow` 또는
`fastparquet` 가 필요하고 둘 다 검증된 requirements.txt(99개, Windows wheel 전수 확인)에 없다.
R5(의존성 추가 시 승인)를 지키기 위해 **엔진이 있으면 parquet, 없으면 CSV** 로 쓴다.
표가 작아서(평가 수백~수만 행) CSV 로도 충분하고, 사람이 바로 열어볼 수 있다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


def parquet_available() -> bool:
    return any(importlib.util.find_spec(m) is not None for m in ("pyarrow", "fastparquet"))


def save_table(df: pd.DataFrame, path_no_ext: Path) -> Path:
    """`<path_no_ext>.parquet` 또는 `<path_no_ext>.csv` 로 저장하고 실제 경로를 돌려준다."""
    path_no_ext.parent.mkdir(parents=True, exist_ok=True)
    if parquet_available():
        p = path_no_ext.with_suffix(".parquet")
        df.to_parquet(p, index=False)
        return p
    p = path_no_ext.with_suffix(".csv")
    df.to_csv(p, index=False, encoding="utf-8")
    return p


def load_table(path_no_ext: Path) -> pd.DataFrame:
    for ext, reader in ((".parquet", pd.read_parquet), (".csv", pd.read_csv)):
        p = path_no_ext.with_suffix(ext)
        if p.exists():
            return reader(p)
    raise FileNotFoundError(f"{path_no_ext}.parquet / .csv 둘 다 없습니다.")
