"""
tests/conftest.py — ensures the repo root is importable and sets a clean test
environment for pytest runs.
"""

import os
import sys
import tempfile

# Put the repo root on sys.path so `import app...` works under pytest.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Default tests to fixture mode and an isolated temp DB so a real run never
# needs an API key and never pollutes a developer's triage.db.
os.environ.setdefault("TRIAGE_MODE", "fixture")
os.environ.setdefault("TRIAGE_DB_PATH", tempfile.mktemp(suffix="_test.db"))
