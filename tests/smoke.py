"""Run the portable synthetic regression suite; no private data required."""
import subprocess
import sys
from pathlib import Path

raise SystemExit(subprocess.call(
    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
    cwd=Path(__file__).resolve().parents[1],
))
