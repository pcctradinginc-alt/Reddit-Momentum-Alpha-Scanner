import os
import sys
from pathlib import Path

# Ensure src/ on path and offline mode for all tests (no network/keys).
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("RMAS_OFFLINE", "true")
os.environ.setdefault("LOG_LEVEL", "WARNING")
