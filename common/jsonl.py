"""Append-only JSONL helpers.

Episode/decision data is written as JSONL with one record per line. Enum and
dataclass values are normalized so records stay JSON-serializable and stable.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator


def _default(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=repr)
    if isinstance(obj, bytes):
        return obj.hex()
    if hasattr(obj, "tolist"):  # numpy scalars/arrays
        return obj.tolist()
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")


def dumps(record: Any) -> str:
    return json.dumps(record, default=_default, ensure_ascii=False, sort_keys=True)


def ensure_trailing_newline(path: str | Path) -> None:
    """Repair a torn trailing line so the next append starts a fresh record.

    A hard kill can leave a half-written final line with no newline. Appending
    to it would concatenate the next record onto the torn bytes, silently
    swallowing a completed result; adding the missing newline isolates the torn
    bytes so a tolerant reader skips them on the next run. Safe on a missing or
    empty file.
    """
    path = Path(path)
    try:
        if path.stat().st_size == 0:
            return
    except FileNotFoundError:
        return
    with path.open("rb") as handle:
        handle.seek(-1, os.SEEK_END)
        last = handle.read(1)
    if last != b"\n":
        with path.open("ab") as handle:
            handle.write(b"\n")


def append_jsonl(path: str | Path, record: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_trailing_newline(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(dumps(record))
        handle.write("\n")


def iter_jsonl(
    path: str | Path, *, tolerate_torn: bool = False
) -> Iterator[dict[str, Any]]:
    """Yield one record per line.

    ``tolerate_torn`` skips unparseable lines instead of raising. Streaming
    resume paths (arena / eval protocol / search agent) set it because a hard
    kill can leave a *torn trailing line* (a half-written JSON record), which is
    expected on resume and must not abort the run (PLAN.md D7). Strict readers
    keep the default so genuine corruption is still surfaced.
    """
    path = Path(path)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                if tolerate_torn:
                    continue
                raise


def read_jsonl(
    path: str | Path, *, tolerate_torn: bool = False
) -> list[dict[str, Any]]:
    return list(iter_jsonl(path, tolerate_torn=tolerate_torn))
