"""Store ↔ engine feature parity helpers (P0, 2026-10-27).

The A2 post-mortem showed that a feature can be *semantically* identical in the
encoder and still differ between the training corpus and online inference:
compact payloads stored ``definition_id / 400000`` as float16, which is not
reversible for six-digit card ids. These helpers make that class of bug
mechanical to detect:

* ``ParityRecorder`` wraps a policy and captures every ``BuiltDecisionContext``
  plus the chosen code (the same records the collector encodes);
* ``compare_observations`` compares two ``EncodedObservation`` values and reports
  the first divergent block/column, with definition columns compared as integer
  ids rather than floats;
* ``player_view_digest`` separates "the replay is not the same game" from "the
  feature function drifted".

The harness is intentionally dependency-light (no engine import at module load)
so unit tests can exercise it without a running game.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

import numpy as np

from reps.observation_encoder import DEFINITION_SCALE, EncodedObservation

def _definition_indices() -> tuple[int, int]:
    from reps.observation_encoder import definition_feature_indices

    return definition_feature_indices()


@dataclass(frozen=True)
class ParityIssue:
    kind: str
    decision: int
    step_index: int | None
    detail: str
    row: int | None = None
    col: int | None = None
    expected: Any = None
    actual: Any = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParityReport:
    ok: bool
    n_decisions: int
    issues: list[ParityIssue] = field(default_factory=list)

    def add(self, issue: ParityIssue) -> None:
        self.issues.append(issue)
        self.ok = False

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for issue in self.issues:
            out[issue.kind] = out.get(issue.kind, 0) + 1
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "n_decisions": int(self.n_decisions),
            "counts": self.counts(),
            "issues": [issue.as_dict() for issue in self.issues],
        }


def player_view_digest(built: Any) -> str:
    """Stable digest of the masked public state for one decision.

    If two replays disagree here, the seed/opponent/state is different and
    comparing features is meaningless; parity must first fix determinism.
    """
    view = getattr(getattr(built, "context", None), "player_view", None)
    if view is None:
        return ""
    try:
        blob = json.dumps(asdict(view), sort_keys=True, default=str, ensure_ascii=True)
    except TypeError:  # pragma: no cover - defensive for non-dataclass views
        blob = repr(view)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _as_matrix(rows: Sequence[Sequence[float]]) -> np.ndarray:
    if not rows:
        return np.zeros((0, 0), dtype=np.float64)
    return np.asarray(rows, dtype=np.float64)


def _recovered_ids(column: np.ndarray) -> np.ndarray:
    return np.rint(column.astype(np.float64) * DEFINITION_SCALE).astype(np.int64)


def _compare_matrix(
    *,
    report: ParityReport,
    decision: int,
    step_index: int | None,
    label: str,
    left: np.ndarray,
    right: np.ndarray,
    definition_index: int,
    atol: float,
    max_issues: int,
) -> None:
    if left.shape != right.shape:
        report.add(
            ParityIssue(
                kind=f"{label.upper()}_SHAPE",
                decision=decision,
                step_index=step_index,
                detail=f"{label} shape {left.shape} != {right.shape}",
                expected=left.shape,
                actual=right.shape,
            )
        )
        return
    if left.size == 0:
        return
    rows = min(left.shape[0], right.shape[0])
    if definition_index < left.shape[1]:
        ids_left = _recovered_ids(left[:rows, definition_index])
        ids_right = _recovered_ids(right[:rows, definition_index])
        bad = np.flatnonzero(ids_left != ids_right)
        for row in bad[:max_issues]:
            report.add(
                ParityIssue(
                    kind=f"{label.upper()}_DEFINITION_ID",
                    decision=decision,
                    step_index=step_index,
                    detail=(
                        f"{label} definition id mismatch at row {int(row)} "
                        f"(column {definition_index})"
                    ),
                    row=int(row),
                    col=int(definition_index),
                    expected=int(ids_right[row]),
                    actual=int(ids_left[row]),
                )
            )
            if len(report.issues) >= max_issues:
                return
    other = [col for col in range(left.shape[1]) if col != definition_index]
    if not other:
        return
    diff = np.abs(left[:rows, other] - right[:rows, other])
    bad = np.argwhere(diff > float(atol))
    for row, col in bad[: max(0, max_issues - len(report.issues))]:
        actual_col = int(other[int(col)])
        report.add(
            ParityIssue(
                kind=f"{label.upper()}_FEATURE",
                decision=decision,
                step_index=step_index,
                detail=(
                    f"{label} feature mismatch at row {int(row)}, column "
                    f"{actual_col} (|Δ|={float(diff[row, col]):.3g} > {atol:g})"
                ),
                row=int(row),
                col=actual_col,
                expected=float(right[row, actual_col]),
                actual=float(left[row, actual_col]),
            )
        )
        if len(report.issues) >= max_issues:
            return


def compare_observations(
    left: EncodedObservation,
    right: EncodedObservation,
    *,
    decision: int = 0,
    step_index: int | None = None,
    atol: float = 1e-3,
    max_issues: int = 20,
) -> ParityReport:
    """Compare two encoded observations, definition ids compared exactly.

    ``left`` is normally the corpus/store observation and ``right`` the freshly
    encoded live observation. A definition mismatch is always fatal (even with a
    loose ``atol``); other floating-point columns use ``atol`` so fp16 storage
    noise does not masquerade as a logic bug.
    """
    report = ParityReport(ok=True, n_decisions=1)
    token_index, option_index = _definition_indices()
    left_tokens, right_tokens = _as_matrix(left.token_features), _as_matrix(right.token_features)
    left_options, right_options = _as_matrix(left.option_features), _as_matrix(right.option_features)
    _compare_matrix(
        report=report,
        decision=decision,
        step_index=step_index,
        label="token",
        left=left_tokens,
        right=right_tokens,
        definition_index=token_index,
        atol=atol,
        max_issues=max_issues,
    )
    _compare_matrix(
        report=report,
        decision=decision,
        step_index=step_index,
        label="option",
        left=left_options,
        right=right_options,
        definition_index=option_index,
        atol=atol,
        max_issues=max_issues,
    )
    if tuple(bool(v) for v in left.token_mask) != tuple(bool(v) for v in right.token_mask):
        report.add(
            ParityIssue(
                kind="TOKEN_MASK",
                decision=decision,
                step_index=step_index,
                detail="token_mask differs",
                expected=tuple(bool(v) for v in right.token_mask),
                actual=tuple(bool(v) for v in left.token_mask),
            )
        )
    if tuple(bool(v) for v in left.option_mask) != tuple(bool(v) for v in right.option_mask):
        report.add(
            ParityIssue(
                kind="OPTION_MASK",
                decision=decision,
                step_index=step_index,
                detail="option_mask differs",
                expected=tuple(bool(v) for v in right.option_mask),
                actual=tuple(bool(v) for v in left.option_mask),
            )
        )
    if tuple(int(v) for v in left.low_level_action_codes) != tuple(
        int(v) for v in right.low_level_action_codes
    ):
        report.add(
            ParityIssue(
                kind="ACTION_CODES",
                decision=decision,
                step_index=step_index,
                detail="legal action codes/order differs",
                expected=tuple(int(v) for v in right.low_level_action_codes),
                actual=tuple(int(v) for v in left.low_level_action_codes),
            )
        )
    return report


@dataclass
class CapturedDecision:
    step_index: int
    request_type: str
    acting_player: int
    code: int
    built: Any
    view_digest: str


@dataclass
class ParityRecorder:
    """Policy wrapper that records each decision for replay/store comparison."""

    policy: Any
    name: str = "parity"
    items: list[CapturedDecision] = field(default_factory=list)

    def __post_init__(self) -> None:
        inner = getattr(self.policy, "name", None)
        if inner and self.name == "parity":
            self.name = f"parity:{inner}"

    def choose(self, built: Any) -> int:
        code = int(self.policy.choose(built))
        context = built.context
        self.items.append(
            CapturedDecision(
                step_index=int(getattr(context, "step_index", -1)),
                request_type=str(getattr(getattr(context, "request_type", None), "value", "")),
                acting_player=int(getattr(context, "acting_player", -1)),
                code=code,
                built=built,
                view_digest=player_view_digest(built),
            )
        )
        return code
