"""Small shared utilities for genshin-GITCG (repo paths, seeding, jsonl, lock)."""

from .paths import REPO_ROOT, data_dir, reports_dir, repo_root
from .seeding import derive_seed, stable_hash
from .jsonl import append_jsonl, iter_jsonl, read_jsonl

__all__ = [
    "REPO_ROOT",
    "repo_root",
    "data_dir",
    "reports_dir",
    "derive_seed",
    "stable_hash",
    "append_jsonl",
    "iter_jsonl",
    "read_jsonl",
]
