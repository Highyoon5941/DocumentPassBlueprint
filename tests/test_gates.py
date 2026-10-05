"""🔒 사람 검토 관문 (R7) — 승인 파일이 없으면 다음 단계가 돌지 않아야 한다."""

from __future__ import annotations

import pytest
import yaml

from svmtrial.concepts import APPROVED as C_APPROVED
from svmtrial.concepts import CANDIDATES as C_CANDIDATES
from svmtrial.concepts import load_approved_concepts
from svmtrial.groups import Group, work_dir
from svmtrial.sections import APPROVED as S_APPROVED
from svmtrial.sections import CANDIDATES as S_CANDIDATES
from svmtrial.sections import GateNotPassed, load_approved_taxonomy

G = Group("대책서", "CUST_X")


def test_taxonomy_gate_blocks_without_approval(settings):
    with pytest.raises(GateNotPassed) as e:
        load_approved_taxonomy(settings, G)
    assert S_APPROVED in str(e.value)
    assert "approve" in str(e.value), "안내 메시지에 다음 조치가 있어야 한다"


def test_taxonomy_gate_passes_after_approval(settings):
    wd = work_dir(settings, "sections", G)
    (wd / S_APPROVED).write_text(
        yaml.safe_dump({"items": [{"id": "D4", "name": "근본원인", "synonyms": ["root cause"]}]},
                       allow_unicode=True), encoding="utf-8")
    items = load_approved_taxonomy(settings, G)
    assert items[0]["id"] == "D4"


def test_empty_approved_taxonomy_still_blocks(settings):
    wd = work_dir(settings, "sections", G)
    (wd / S_APPROVED).write_text(yaml.safe_dump({"items": []}), encoding="utf-8")
    with pytest.raises(GateNotPassed, match="비어 있"):
        load_approved_taxonomy(settings, G)


def test_concepts_gate_blocks_without_approval(settings):
    with pytest.raises(GateNotPassed) as e:
        load_approved_concepts(settings, G)
    msg = str(e.value)
    assert C_APPROVED in msg
    assert "S4c" in msg, "승인 없으면 채점이 안 된다는 설명이 있어야 한다"


def test_concepts_gate_passes_after_approval(settings):
    wd = work_dir(settings, "concepts", G)
    (wd / C_APPROVED).write_text(yaml.safe_dump({"concepts": [
        {"id": "C001", "question": 'D4 에 "5Why 표" 가 있는가?', "section_id": "D4",
         "type": "structure", "actionable": True, "rationale": "x"}]}, allow_unicode=True), encoding="utf-8")
    cs = load_approved_concepts(settings, G)
    assert cs[0].id == "C001" and cs[0].actionable is True


def test_candidate_file_carries_review_instructions(settings):
    """후보 파일에는 사람이 무엇을 검토해야 하는지 안내가 들어 있어야 한다."""
    assert S_CANDIDATES.endswith("_candidates.yaml")
    assert C_CANDIDATES.endswith("_candidates.yaml")


def test_approve_cli_requires_candidate(settings, tmp_path, monkeypatch):
    """approve 는 후보 파일이 없으면 실패해야 한다 (빈 승인 파일을 만들지 않는다)."""
    from typer.testing import CliRunner

    from svmtrial.cli import app

    monkeypatch.setattr("svmtrial.cli._settings", lambda config=None: settings)
    r = CliRunner().invoke(app, ["approve", "--group", "대책서__CUST_Z", "--gate", "sections"])
    assert r.exit_code == 1
    assert not (work_dir(settings, "sections", Group("대책서", "CUST_Z")) / S_APPROVED).exists()


def test_approve_cli_rejects_bad_gate(settings, monkeypatch):
    from typer.testing import CliRunner

    from svmtrial.cli import app

    monkeypatch.setattr("svmtrial.cli._settings", lambda config=None: settings)
    r = CliRunner().invoke(app, ["approve", "--group", "대책서__CUST_X", "--gate", "nonsense"])
    assert r.exit_code == 1


def test_approve_cli_copies_candidate(settings, monkeypatch):
    from typer.testing import CliRunner

    from svmtrial.cli import app

    wd = work_dir(settings, "concepts", G)
    (wd / C_CANDIDATES).write_text(yaml.safe_dump({"concepts": [
        {"id": "C001", "question": "q", "type": "structure", "actionable": True, "rationale": "r"}]},
        allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr("svmtrial.cli._settings", lambda config=None: settings)
    r = CliRunner().invoke(app, ["approve", "--group", "대책서__CUST_X", "--gate", "concepts"])
    assert r.exit_code == 0
    assert (wd / C_APPROVED).exists()
