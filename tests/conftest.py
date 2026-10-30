"""Shared pytest fixtures / path setup for genshin-GITCG.

The repo is used as a flat collection of top-level packages (``envs``, ``reps``,
``agents`` ...). ``pyproject.toml`` already adds the repo root to ``sys.path``;
this file keeps that working when pytest is invoked from elsewhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
