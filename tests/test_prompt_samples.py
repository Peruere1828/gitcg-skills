"""The committed prompt samples are evidence: pin the directory to exactly two files.

``reports/prompts/`` used to collect one-off renders from different code paths, so
a reader could not tell which bytes a model actually saw. It now holds only the two
real-call samples, and this test keeps it that way: a stray file means the directory
is being used as scratch space again (write those to ``/tmp/opencode/``), which is
exactly the drift that motivated the cleanup. Asserting the exact entry set is
deliberately strict; if a legitimate second artifact ever appears, extend the set
here instead of loosening the check to "no unexpected markdown".
"""

from __future__ import annotations

import re
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[1] / "reports/prompts"
EXPECTED = {"decision_prompt.md", "reflection_prompt.md"}
FENCE = "````"


def test_directory_holds_exactly_the_two_samples() -> None:
    entries = {path.name for path in PROMPTS.iterdir()}
    assert entries == EXPECTED, f"unexpected entries in reports/prompts: {entries - EXPECTED}"
    assert not any(path.is_dir() for path in PROMPTS.iterdir())


def test_samples_are_non_empty_and_have_step_sections() -> None:
    for name in sorted(EXPECTED):
        text = (PROMPTS / name).read_text(encoding="utf-8")
        assert len(text) > 2000, f"{name} looks truncated ({len(text)} chars)"
        # The kept call may be any step (e.g. the stage with the most failures),
        # so only require that a system/user pair is present.
        # The decision sample hoists one shared SYSTEM section; the reflection
        # sample keeps per-call system blocks.
        assert "## SYSTEM（" in text or re.search(
            r"^### step \d+ · system", text, re.M
        ), f"{name}: missing system block"
        assert re.search(r"^### step \d+ · user", text, re.M), (
            f"{name}: missing user block"
        )
        # every fence pair must be balanced, otherwise the "verbatim" claim is void
        assert text.count(f"\n{FENCE}\n") % 2 == 0, f"{name}: unbalanced {FENCE} fences"


def test_decision_sample_hoists_the_shared_system_message() -> None:
    """The system message is byte-identical across calls, so it is stored once.

    A 58-call file with a copy per call was too large to review; the deduplicated
    form keeps the evidence intact (full prompt = shared SYSTEM + per-step USER).
    """
    text = (PROMPTS / "decision_prompt.md").read_text(encoding="utf-8")
    assert "全部调用去重后的等价形式" in text
    assert text.count("## SYSTEM（") == 1, "expected exactly one hoisted SYSTEM section"
    assert "### step 1 · system" not in text, "system must not be duplicated per step"
    assert "### step 1 · user（" in text, "user blocks must carry method + char count"
    assert text.count("· user（") >= 2, "expected several per-call USER blocks"
