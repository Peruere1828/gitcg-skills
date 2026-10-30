"""A2 identity embeddings: recovery, bucketing, model wiring, backward compatibility.

The encoder writes a definition id into one feature column as ``id / 400000``. These
tests pin that contract down, because the whole "no re-collection" argument rests on
the scalar being exactly invertible, and because a silent index drift would make the
model learn from a garbage column (round number, a ratio, ...) instead of card identity.
"""

from __future__ import annotations

import torch

from reps.id_features import bucket_ids, definition_ids, option_definition_ids, token_definition_ids
from reps.observation_encoder import (
    DEFINITION_SCALE,
    TokenObservationEncoder,
    TokenObservationEncoderConfig,
    definition_feature_indices,
)
from train.model import (
    CVPN,
    CVPNConfig,
    TransformerCVPN,
    TransformerCVPNConfig,
    load_state_dict_compatible,
    zero_init_identity_branch,
)


def _encoder() -> TokenObservationEncoder:
    return TokenObservationEncoder(
        TokenObservationEncoderConfig(
            card_vocabulary=(311328, 332031, 333008),
            character_vocabulary=(1115, 1116),
        )
    )


def test_definition_index_matches_the_encoder_layout():
    index, _ = definition_feature_indices()
    encoder = _encoder()
    features = encoder._build_token(
        token_type="hand_card", owner="self", definition_id=311328, round_number=3
    )
    assert len(features) == encoder.token_dim
    assert abs(features[index] - 311328 / DEFINITION_SCALE) < 1e-9
    # the neighbouring scalar is the round number, so an off-by-one cannot pass silently
    assert abs(features[index - 1] - 3 / 15.0) < 1e-9


def test_option_definition_index_matches_the_encoder_layout():
    _, index = definition_feature_indices()
    from reps.event_features import EventFeatureEncoder
    from reps.schema import DecisionType, LowLevelActionSpec, OptionKind

    encoder = _encoder()
    spec = LowLevelActionSpec(
        action_code=0,
        request_type=DecisionType.ACTION,
        kind=OptionKind.ACTION_PLAY_CARD,
        label="test",
        subject_definition_id=332031,
    )
    # ``encode_action_descriptor`` is the state-free path but shares the exact layout
    # with the public encoder used for corpora, so the column index is the same.
    features = encoder.event.encode_action_descriptor(spec)
    assert len(features) == encoder.option_dim
    assert abs(features[index] - 332031 / DEFINITION_SCALE) < 1e-9


def test_definition_ids_round_trip_in_float32():
    ids = torch.tensor([0, 1, 1115, 2402, 211133, 311328, 400000], dtype=torch.float64)
    column = (ids / DEFINITION_SCALE).to(torch.float32)
    assert torch.equal(definition_ids(column), ids.long())


def test_bucket_ids_keeps_zero_and_stays_in_range():
    ids = torch.tensor([0, 1, 7, 511, 512, 9999])
    buckets = bucket_ids(ids, 512)
    assert buckets[0].item() == 0
    assert bool(((buckets[1:] >= 1) & (buckets[1:] < 512)).all())


def _batch(b=2, t=5, k=4):
    tokens = torch.zeros(b, t, 65)
    token_mask = torch.ones(b, t, dtype=torch.bool)
    options = torch.zeros(b, k, 68)
    option_mask = torch.ones(b, k, dtype=torch.bool)
    return tokens, token_mask, options, option_mask


def test_identity_embeddings_change_the_logits():
    """Two inputs differing only in the definition scalar must not be equivalent."""
    torch.manual_seed(0)
    model = CVPN(CVPNConfig(token_dim=65, option_dim=68, d_model=64, id_buckets=512))
    tokens, token_mask, options, option_mask = _batch()
    index, option_index = definition_feature_indices()
    tokens[0, 1, index] = 311328 / DEFINITION_SCALE
    tokens[1, 1, index] = 332031 / DEFINITION_SCALE
    options[:, 0, option_index] = 311328 / DEFINITION_SCALE
    with torch.no_grad():
        logits, _ = model(tokens, token_mask, options, option_mask)
    assert not torch.allclose(logits[0], logits[1]), "card identity is ignored"


def test_embeddings_receive_gradients():
    torch.manual_seed(0)
    model = CVPN(CVPNConfig(token_dim=65, option_dim=68, d_model=64, id_buckets=512))
    tokens, token_mask, options, option_mask = _batch()
    index, option_index = definition_feature_indices()
    tokens[:, :, index] = 311328 / DEFINITION_SCALE
    options[:, :, option_index] = 332031 / DEFINITION_SCALE
    logits, _ = model(tokens, token_mask, options, option_mask)
    torch.nn.functional.cross_entropy(logits, torch.tensor([0, 1])).backward()
    assert model.token_id_emb.weight.grad is not None
    assert float(model.token_id_emb.weight.grad.abs().sum()) > 0
    assert float(model.option_id_emb.weight.grad.abs().sum()) > 0


def test_historical_checkpoint_still_loads():
    """``id_buckets=0`` must reproduce the pre-A2 module set exactly."""
    legacy = CVPN(CVPNConfig(token_dim=65, option_dim=68, d_model=64))
    assert not any("id_emb" in name for name in legacy.state_dict())
    tokens, token_mask, options, option_mask = _batch()
    with torch.no_grad():
        logits, _ = legacy(tokens, token_mask, options, option_mask)
    assert torch.isfinite(logits).all()


def test_transformer_supports_identity_embeddings():
    torch.manual_seed(0)
    model = TransformerCVPN(
        TransformerCVPNConfig(
            token_dim=65, option_dim=68, d_model=64, n_layers=1, n_heads=4, id_buckets=256
        )
    )
    tokens, token_mask, options, option_mask = _batch()
    index, _ = definition_feature_indices()
    tokens[:, 0, index] = 211133 / DEFINITION_SCALE
    logits, value = model(tokens, token_mask, options, option_mask)
    assert torch.isfinite(logits).all() and torch.isfinite(value).all()


def test_token_and_option_id_helpers_agree_with_the_models_view():
    tokens, _, options, _ = _batch()
    index, option_index = definition_feature_indices()
    tokens[0, 0, index] = 311328 / DEFINITION_SCALE
    options[0, 0, option_index] = 332031 / DEFINITION_SCALE
    token_ids = token_definition_ids(tokens, index, 512)
    option_ids = option_definition_ids(options, option_index, 512)
    assert token_ids[0, 0].item() == bucket_ids(torch.tensor([311328]), 512)[0].item()
    assert option_ids[0, 0].item() == bucket_ids(torch.tensor([332031]), 512)[0].item()
    assert token_ids[0, 1].item() == 0        # no definition -> padding bucket


def test_legacy_checkpoint_upgrades_to_identity_model_as_a_noop():
    """A2 fine-tune path: old weights load unchanged, id branch contributes 0."""
    torch.manual_seed(0)
    legacy = CVPN(CVPNConfig(token_dim=65, option_dim=68, d_model=64))
    model = CVPN(CVPNConfig(token_dim=65, option_dim=68, d_model=64, id_buckets=512))
    assert zero_init_identity_branch(model)
    missing = load_state_dict_compatible(model, legacy.state_dict())
    assert missing
    assert all(
        key.startswith(("token_id_emb.", "option_id_emb.", "id_proj.", "option_id_proj."))
        for key in missing
    )

    legacy.eval()
    model.eval()
    tokens, token_mask, options, option_mask = _batch()
    index, option_index = definition_feature_indices()
    tokens[:, :, index] = 311328 / DEFINITION_SCALE
    options[:, :, option_index] = 332031 / DEFINITION_SCALE
    with torch.no_grad():
        before, _ = legacy(tokens, token_mask, options, option_mask)
        after, _ = model(tokens, token_mask, options, option_mask)
    assert torch.allclose(before, after, atol=1e-6), "zero-init identity branch is not a no-op"


def test_id_compatible_load_rejects_non_id_gaps():
    legacy = CVPN(CVPNConfig(token_dim=65, option_dim=68, d_model=64))
    model = CVPN(CVPNConfig(token_dim=65, option_dim=68, d_model=64, id_buckets=512))
    state = dict(legacy.state_dict())
    state.pop("token_proj.0.weight")
    try:
        load_state_dict_compatible(model, state)
    except RuntimeError as exc:
        assert "token_proj.0.weight" in str(exc)
    else:
        raise AssertionError("a non-identity missing tensor must be rejected")
