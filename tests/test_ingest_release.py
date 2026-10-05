"""S1 형식 판정 로직과 배포 zip 의 비밀정보 차단."""

from __future__ import annotations

import pytest

from svmtrial.ingest import _format_from_creator, _format_from_ratio


@pytest.mark.parametrize("creator,producer,expected", [
    ("Microsoft PowerPoint", "", "slides"),
    ("", "Keynote", "slides"),
    ("Google Slides", "", "slides"),
    ("Microsoft Word", "", "docs"),
    ("Hancom HWP", "", "docs"),
    ("한글", "", "docs"),
    ("Google Docs", "", "docs"),
    ("Canon ScanFront", "generic scanner", None),
    ("", "", None),
])
def test_format_from_creator(creator, producer, expected):
    assert _format_from_creator(creator, producer) == expected


@pytest.mark.parametrize("ratios,expected", [
    ([1.78, 1.78, 1.78, 1.78], "slides"),
    ([0.71, 0.71, 0.71, 0.71], "docs"),
    ([1.78, 0.71, 1.78, 0.71], None),      # 혼재 → Gemini 판정으로 넘긴다
    ([], None),
])
def test_format_from_ratio(ratios, expected):
    got, conf = _format_from_ratio(ratios, 1.25)
    assert got == expected
    assert 0.0 <= conf <= 1.0


def test_format_from_ratio_threshold_boundary():
    # 임계 비율 1.25 바로 위/아래
    assert _format_from_ratio([1.26] * 4, 1.25)[0] == "slides"
    assert _format_from_ratio([1.24] * 4, 1.25)[0] == "docs"


# ---------------------------------------------------------------- 배포 보안 (R3)


def test_release_forbidden_patterns():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("brel", root / "scripts" / "build_release.py")
    brel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(brel)

    must_block = [
        ".env", "config/.env", "scripts/sub/.env",
        "config/svmtrial-sa.json", "keys/service_account.json", "config/credentials.json",
        "secret.json.key", "data/raw/pdfs/x.pdf", "work/cache/a.json",
        "outputs/run/x.docx", "logs/run/gemini_calls.jsonl", "dist/old.zip",
        "src/__pycache__/x.pyc",
        "src/svmtrial.egg-info/PKG-INFO", "src/svmtrial.egg-info/SOURCES.txt",
    ]
    for rel in must_block:
        assert brel.is_forbidden(rel), f"차단되어야 함: {rel}"

    must_allow = ["src/svmtrial/cli.py", "prompts/ocr_page.v1.md", "config/config.yaml",
                  ".env.example", "requirements.txt", "docs/Runbook.md", "windows/run_windows.bat"]
    for rel in must_allow:
        assert not brel.is_forbidden(rel), f"포함되어야 함: {rel}"


# ---------------------------------------------------------------- ingest 이슈 누적


def test_ingest_issues_accumulate_across_groups(settings):
    """그룹마다 전역 ingest_issues.csv 를 덮어쓰면 마지막 그룹의 이슈만 남는다 (회귀 방지).

    SETUP.md §7.1 은 단일 파일을 요구하므로, 모든 그룹의 이슈가 합쳐져 있어야 한다.
    """
    import pandas as pd

    from svmtrial.groups import Group
    from svmtrial.ingest import _write_issues

    ga, gb = Group("대책서", "CUST_A"), Group("대책서", "CUST_B")
    _write_issues(settings, ga, [
        {"doc_id": "CUST_A_041", "issue": "PDF는 있으나 평가 시트에 없음", "path": ""},
        {"doc_id": "CUST_A_999", "issue": "평가 시트에는 있으나 PDF 없음", "path": ""},
    ])
    _write_issues(settings, gb, [
        {"doc_id": "CUST_B_041", "issue": "PDF는 있으나 평가 시트에 없음", "path": ""},
        {"doc_id": "CUST_B_999", "issue": "평가 시트에는 있으나 PDF 없음", "path": ""},
    ])

    f = settings.work / "ingest_issues.csv"
    df = pd.read_csv(f)
    assert len(df) == 4, f"두 그룹의 이슈 4건이 모두 남아야 한다 (실제 {len(df)}건)"
    assert set(df["doc_id"]) == {"CUST_A_041", "CUST_A_999", "CUST_B_041", "CUST_B_999"}
    assert set(df["group"]) == {"대책서__CUST_A", "대책서__CUST_B"}


def test_ingest_issues_rerun_is_idempotent(settings):
    """한 그룹만 다시 ingest 해도 다른 그룹 이슈가 사라지지 않고 중복도 생기지 않는다."""
    import pandas as pd

    from svmtrial.groups import Group
    from svmtrial.ingest import _write_issues

    ga, gb = Group("대책서", "CUST_A"), Group("대책서", "CUST_B")
    a_rows = [{"doc_id": "CUST_A_041", "issue": "PDF는 있으나 평가 시트에 없음", "path": ""}]
    _write_issues(settings, ga, a_rows)
    _write_issues(settings, gb, [{"doc_id": "CUST_B_041", "issue": "PDF는 있으나 평가 시트에 없음", "path": ""}])
    _write_issues(settings, ga, a_rows)          # A 만 재실행

    df = pd.read_csv(settings.work / "ingest_issues.csv")
    assert len(df) == 2, f"중복이나 소실이 있다 (실제 {len(df)}건)"
    assert set(df["doc_id"]) == {"CUST_A_041", "CUST_B_041"}


def test_ingest_issues_cleared_when_resolved(settings):
    """이슈가 해소되면 전역 파일도 사라져야 한다 (오래된 경고가 남지 않게)."""
    from svmtrial.groups import Group
    from svmtrial.ingest import _write_issues

    g = Group("대책서", "CUST_A")
    _write_issues(settings, g, [{"doc_id": "X", "issue": "임시", "path": ""}])
    assert (settings.work / "ingest_issues.csv").exists()
    _write_issues(settings, g, [])
    assert not (settings.work / "ingest_issues.csv").exists()
