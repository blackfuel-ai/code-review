"""Make the action's modules importable from tests.

bf_review_trace is bundled next to the review scripts at the repo root, so we
prepend that directory to sys.path for both the trace module and the review
scripts under test.
"""

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
