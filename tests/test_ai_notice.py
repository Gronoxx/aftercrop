# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""M9.4: AI_USE.md exists and every code file carries the SI notice.

Vendored libraries and test fixtures are third-party or data files and are left out.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTICE = "Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md."
CODE = (".py", ".mjs", ".css", ".html", ".yaml", ".yml")


def tracked_code():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    for f in out.split():
        if f.startswith(("site/vendor/", "tests/fixtures/")):
            continue
        if f.endswith(CODE) or f == "Makefile":
            yield f


def test_ai_use_exists():
    text = (ROOT / "AI_USE.md").read_text(encoding="utf-8")
    assert "Claude" in text


def test_every_code_file_has_the_notice():
    files = list(tracked_code())
    assert len(files) > 50
    missing = [f for f in files if NOTICE not in (ROOT / f).read_text(encoding="utf-8")[:600]]
    assert missing == [], f"files without the SI notice: {missing}"
