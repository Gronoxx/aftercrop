# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""M5.8, which field first: runs the node tests of fieldOrder in site/js/decision.mjs.

The rule lives only in decision.mjs, so this file only calls node.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_field_order():
    r = subprocess.run(["node", "--test", "tests/field_order.test.mjs"], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
