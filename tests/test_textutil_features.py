"""문자열 정규화, 섹션 매칭, 구조 특징, Kendall τ."""

from __future__ import annotations

import pandas as pd
import pytest

from svmtrial import textutil as tu
from svmtrial.features import is_binary, is_structural
from svmtrial.sections import order_kendall_tau
from svmtrial.seeds import SEED_8D, seed_for


@pytest.mark.parametrize("raw,expected", [
    ("1. D4 Root Cause", "d4 root cause"),
    ("  (3)  근본원인  ", "근본원인"),
    ("가) 효과검증", "효과검증"),
    ("D4. 근본원인(발생/유출)", "d4 근본원인 발생 유출"),
])
def test_norm(raw, expected):
    assert tu.norm(raw) == expected


def test_quoted_phrase():
    assert tu.quoted_phrase('D4 섹션에 "5Why 분석 표" 가 있는가?') == "5Why 분석 표"
    assert tu.quoted_phrase("인용 없음") is None


def test_contains_tokens():
    hay = "D4 Root cause 5-Why analysis table Why1 Why2"
    assert tu.contains_tokens(hay, "5-Why analysis table", ratio=0.7)
    assert not tu.contains_tokens(hay, "horizontal deployment table", ratio=0.7)


@pytest.mark.parametrize("title,expected", [
    ("D4 Root cause", "D4"),
    ("근본원인 분석", "D4"),
    ("D7 Prevent recurrence", "D7"),
    ("수평전개", "D7"),
    ("효과검증", "D6"),
    ("전혀 관계없는 제목 블라블라", "OTHER"),
])
def test_best_section(title, expected):
    assert tu.best_section(title, SEED_8D) == expected


def test_seed_for_unknown_doc_type():
    assert seed_for("대책서")[0]["id"] == "D0"
    assert seed_for("존재하지않는문서") == []


def test_kendall_tau_order():
    std = ["D1", "D2", "D3", "D4"]
    assert order_kendall_tau(["D1", "D2", "D3", "D4"], std) == 1.0
    assert order_kendall_tau(["D4", "D3", "D2", "D1"], std) == -1.0
    assert order_kendall_tau(["D1"], std) is None
    assert order_kendall_tau([], std) is None


def test_is_binary_and_structural():
    assert is_binary(pd.Series([0.0, 1.0, 0.0]))
    assert is_binary(pd.Series([0.0, 1.0, float("nan")]))
    assert not is_binary(pd.Series([0.0, 2.0]))
    assert is_structural("STR_n_pages") and is_structural("SEC_D4")
    assert not is_structural("C001")
