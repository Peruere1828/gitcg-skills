"""Engine / card-pool version locking (PLAN.md D4).

Every run must reference a copied ``engine.lock`` capturing the git commit of the
engine used to produce it plus the card pool version. This module loads the lock
and stamps run metadata with it.
"""

from __future__ import annotations

import platform
import subprocess
import sys
try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python <=3.10 (e.g. the cluster): tomli backport
    import tomli as tomllib  # type: ignore
from pathlib import Path
from typing import Any

from .paths import REPO_ROOT


def _git_commit(path: Path) -> str | None:
    try:
        return (
            subprocess.check_output(
                ["git", "-C", str(path), "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            or None
        )
    except Exception:
        return None


def load_engine_lock(path: str | Path | None = None) -> dict[str, Any]:
    """Load an engine.lock TOML, or return a best-effort live description."""
    lock_path = Path(path) if path is not None else REPO_ROOT / "configs" / "engine.lock"
    if lock_path.exists():
        with lock_path.open("rb") as handle:
            return tomllib.load(handle)
    return {}


def runtime_metadata(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Best-effort environment stamp for run metadata (never contains secrets)."""
    try:
        from importlib.metadata import version as _pkg_version

        gitcg_version = _pkg_version("gitcg")
    except Exception:
        gitcg_version = "unknown"
    refs_engine = REPO_ROOT / "refs" / "genius-invokation"
    meta: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "gitcg_version": gitcg_version,
        "engine_ref_commit": _git_commit(refs_engine),
    }
    if extra:
        meta.update(extra)
    return meta
