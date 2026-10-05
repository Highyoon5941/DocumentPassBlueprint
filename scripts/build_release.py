"""Windows 배포 zip 빌드 (SETUP.md §11.1). Ubuntu 에서 실행한다.

포함: src/ prompts/ config/ scripts/ windows/ requirements.txt environment.yml
      pyproject.toml .env.example docs/*.md
제외: R3 의 모든 항목 (.env, 인증키, data/, work/, outputs/, dist/, logs/)

사용:
  python scripts/build_release.py
  python scripts/build_release.py --with-wheels     # 사내망 오프라인 설치용 (약 160MB)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.M)

INCLUDE_DIRS = ["src", "prompts", "config", "scripts", "windows", "docs"]
INCLUDE_FILES = ["requirements.txt", "environment.yml", "pyproject.toml", ".env.example", "CLAUDE.md"]

# R3: 절대 들어가면 안 되는 것 (들어가면 빌드를 실패시킨다)
FORBIDDEN_NAMES = {".env"}
FORBIDDEN_PATTERNS = [
    re.compile(r"\.env$"),
    re.compile(r"-sa\.json$"),
    re.compile(r"\.json\.key$"),
    re.compile(r"(^|/)(data|work|outputs|dist|logs)/"),
    re.compile(r"__pycache__|\.pyc$|\.ipynb_checkpoints|\.pytest_cache|\.ruff_cache"),
    re.compile(r"\.egg-info/|\.eggs/"),            # pip install -e . 가 남기는 빌드 산물
    re.compile(r"(^|/)credentials?\.json$"),
    re.compile(r"(^|/)service[-_]account.*\.json$"),
]


def version() -> str:
    m = VERSION_RE.search((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return m.group(1) if m else "0.0.0"


def collect() -> list[Path]:
    out: list[Path] = []
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for f in sorted(base.rglob("*")):
            if f.is_file():
                out.append(f)
    for name in INCLUDE_FILES:
        f = ROOT / name
        if f.exists():
            out.append(f)
    return out


def is_forbidden(rel: str) -> str | None:
    if Path(rel).name in FORBIDDEN_NAMES:
        return f"파일명 금지: {rel}"
    for pat in FORBIDDEN_PATTERNS:
        if pat.search(rel):
            return f"패턴 금지({pat.pattern}): {rel}"
    return None


def download_wheels(dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "download",
           "--platform", "win_amd64", "--python-version", "3.11",
           "--only-binary=:all:", "-r", str(ROOT / "requirements.txt"), "-d", str(dest)]
    print("$", " ".join(cmd))
    r = subprocess.run(cmd, check=False)
    if r.returncode != 0:
        print("[X] wheel 다운로드 실패 — 사내 프록시/SSL 설정을 확인하세요 (SETUP.md §16).")
        return 0
    return len(list(dest.glob("*.whl")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-wheels", action="store_true",
                    help="Windows wheel 을 미리 받아 wheelhouse/ 에 넣는다 (오프라인 설치용)")
    ap.add_argument("--out", default=None, help="zip 경로 (기본: dist/valeo_svmtrial_<version>.zip)")
    a = ap.parse_args()

    v = version()
    out = Path(a.out) if a.out else ROOT / "dist" / f"valeo_svmtrial_{v}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)

    files = collect()
    entries: list[tuple[Path, str]] = []
    problems: list[str] = []
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        why = is_forbidden(rel)
        if why:
            problems.append(why)
            continue
        entries.append((f, rel))

    wheel_dir = ROOT / "build" / "wheelhouse"
    n_wheels = 0
    if a.with_wheels:
        n_wheels = download_wheels(wheel_dir)
        for w in sorted(wheel_dir.glob("*.whl")):
            entries.append((w, f"wheelhouse/{w.name}"))

    # 보안 검사: 금지 항목이 목록에 남아 있으면 실패 처리 (§11.1)
    hard_fail = [p for p in problems if ".env" in p or "sa.json" in p or "credential" in p.lower()]
    if hard_fail:
        print("[X] 빌드 실패 — 비밀정보가 포함될 수 있는 파일이 감지됐습니다:")
        for p in hard_fail:
            print("   ", p)
        return 1

    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f, rel in entries:
            z.write(f, rel)

    # zip 내부 재검사
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    bad = [n for n in names if is_forbidden(n) and not n.startswith("wheelhouse/")]
    if bad:
        out.unlink()
        print("[X] 빌드 실패 — zip 안에 금지 항목이 있습니다:", bad[:5])
        return 1

    size = out.stat().st_size / 1e6
    print(f"\n[OK] {out.relative_to(ROOT)}  ({size:.1f} MB, 파일 {len(names)}개"
          + (f", wheel {n_wheels}개" if a.with_wheels else "") + ")")
    print("\n포함된 항목:")
    for d in INCLUDE_DIRS + INCLUDE_FILES:
        n = sum(1 for x in names if x == d or x.startswith(d + "/"))
        if n:
            print(f"   {d:22s} {n}개")
    if a.with_wheels:
        print(f"   {'wheelhouse':22s} {n_wheels}개")
    print("\n제외 확인: .env / 인증키 / data / work / outputs / logs / dist  → 없음 ✓")
    print("\n다음 단계 (Windows PC):")
    print("  1) zip 을 풀고 Anaconda Prompt 에서  windows\\setup_windows.bat")
    print("  2) .env 편집 (SVMTRIAL_BACKEND=vertex, 프로젝트 ID, 모델 ID)")
    print("  3) gcloud auth application-default login   (인증 방식 A)")
    print("  4) windows\\run_windows.bat check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
