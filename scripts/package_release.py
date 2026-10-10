"""Build a clean ShopDetective_Project.zip from an allow-list of project files.

Excludes .env, real secrets, virtual environments, caches, temp files, uploads,
.git and previous archives. Run:  python scripts/package_release.py
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "ShopDetective_Project.zip"
TOP = "ShopDetective"

INCLUDE_DIRS = [
    "shopdetective",
    "scripts",
    "data",
    "tests",
    "docs",
    "assets",
    "reference",
    ".streamlit",
    ".github",
    "reports/evaluation/historical",
]
INCLUDE_FILES = [
    "app.py",
    "README.md",
    "CLAUDE.md",
    "BUILD_STATUS.md",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    ".env.example",
    ".gitignore",
    "ShopDetective_Claude_Build_Brief.md",
    "reports/evaluation/latest.json",
    "reports/evaluation/latest.md",
]
EXCLUDE_PARTS = {".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".git", "dist", "uploads"}
EXCLUDE_NAMES = {".env", "secrets.toml", ".DS_Store", "Thumbs.db"}
EXCLUDE_SUFFIXES = {".pyc", ".log", ".zip", ".tmp"}


def wanted(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDE_PARTS for part in rel.parts):
        return False
    return path.name not in EXCLUDE_NAMES and path.suffix not in EXCLUDE_SUFFIXES


def collect() -> list[Path]:
    files: list[Path] = []
    for d in INCLUDE_DIRS:
        files += [p for p in sorted((ROOT / d).rglob("*")) if p.is_file() and wanted(p)]
    for f in INCLUDE_FILES:
        p = ROOT / f
        if p.is_file() and wanted(p):
            files.append(p)
    return files


def check_no_secrets(files: list[Path]) -> None:
    for p in files:
        if p.suffix in {".png", ".svg"}:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "AIza" in text and p.name not in {"package_release.py", "test_ai_contracts.py", "test_review_and_report.py"}:
            sys.exit(f"Refusing to package: possible API key in {p.relative_to(ROOT)}")


def main() -> int:
    files = collect()
    check_no_secrets(files)
    if OUT.exists():
        OUT.unlink()
    with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, f"{TOP}/{p.relative_to(ROOT).as_posix()}")
    print(f"Wrote {OUT.name}: {len(files)} files, {OUT.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
