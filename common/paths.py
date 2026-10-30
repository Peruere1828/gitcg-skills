"""Repository path helpers.

Repo layout is defined in PLAN.md §9. Paths are resolved relative to this file so
that scripts work regardless of the current working directory.
"""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root (the directory containing PLAN.md)."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "PLAN.md").exists():
            return parent
    # Fallback: common/ -> repo root
    return here.parents[1]


REPO_ROOT = repo_root()


def data_dir(*parts: str) -> Path:
    """Path under ``data/`` (gitignored content, kept local)."""
    root = Path(os.environ.get("GITCG_DATA_DIR", REPO_ROOT / "data"))
    return root.joinpath(*parts)


def reports_dir(*parts: str) -> Path:
    """Path under ``reports/`` (small, committed artifacts)."""
    root = Path(os.environ.get("GITCG_REPORTS_DIR", REPO_ROOT / "reports"))
    return root.joinpath(*parts)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
