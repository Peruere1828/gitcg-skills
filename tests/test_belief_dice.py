"""P0: hidden dice are sampled i.i.d., not emitted as a deterministic rotation."""

from __future__ import annotations

from reps.belief import _sample_hidden_dice


def test_hidden_dice_sampling_is_deterministic_per_particle():
    assert _sample_hidden_dice(sample_index=7, count=8) == _sample_hidden_dice(
        sample_index=7, count=8
    )


def test_hidden_dice_can_repeat_instead_of_being_a_rotation():
    # The old implementation returned eight distinct dice for count=8. Real
    # dice pools are drawn with replacement, so at least one of 100 particles
    # must contain a duplicate.
    draws = [_sample_hidden_dice(sample_index=i, count=8) for i in range(100)]
    assert any(len(set(draw)) < len(draw) for draw in draws)
