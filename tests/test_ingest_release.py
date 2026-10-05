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
