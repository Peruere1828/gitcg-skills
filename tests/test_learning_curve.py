"""CVPN learning-curve sweep: size filtering and a tiny end-to-end smoke."""

from __future__ import annotations

from envs.observation import default_encoder
from train.learning_curve import curve_sizes, main, run_learning_curve
from train.train_loop import TrainConfig


def test_curve_sizes_filters_dedupes_and_sorts():
    assert curve_sizes(100, [512, 32, 32, 64, 0, 100]) == [32, 64, 100]


def test_eval_opponents_without_seeds_is_an_error():
    import pytest

    with pytest.raises(SystemExit):
        main(["--eval-opponents", "natlan_battleship"])


def test_learning_curve_tiny_sweep():
    encoder = default_encoder()
    report = run_learning_curve(
        deck="superconduct_aggro",
        train_opponents=("natlan_battleship",),
        train_seeds=(0,),
        sizes=(8, 16),
        d_models=(16,),
        train_config=TrainConfig(epochs=2, batch_size=8, val_fraction=0.25),
        encoder=encoder,
    )
    points = report["points"]
    assert [p["n_samples"] for p in points] == [8, 16]
    assert report["sizes"] == [8, 16]
    assert report["pool_stats"]["n"] >= 16
    assert report["n_val_holdout"] > 0
    for point in points:
        assert point["d_model"] == 16
        assert point["n_train"] == point["n_samples"]  # val is the fixed holdout
        assert point["n_val"] == report["n_val_holdout"]  # same holdout every point
        assert 0.0 <= point["train_accuracy"] <= 1.0
        assert 0.0 <= point["val_accuracy"] <= 1.0
        assert len(point["history"]) == 2
