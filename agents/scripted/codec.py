# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

import base64


def decode_share_code_raw(share_code: str) -> tuple[int, ...]:
    data = list(base64.b64decode(share_code.encode("ascii")))
    if len(data) != 51:
        raise ValueError(f"invalid share code length: {len(data)}")
    last = data.pop()
    reordered = [((data[2 * i] - last) & 0xFF) for i in range(25)] + [
        ((data[2 * i + 1] - last) & 0xFF) for i in range(25)
    ] + [0]
    result: list[int] = []
    for index in range(17):
        result.append((reordered[index * 3] << 4) + (reordered[index * 3 + 1] >> 4))
        result.append(((reordered[index * 3 + 1] & 0xF) << 8) + reordered[index * 3 + 2])
    result.pop()
    return tuple(result)


def decode_share_code(share_code: str, share_to_id: dict[int, int]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    share_ids = decode_share_code_raw(share_code)
    definition_ids = [share_to_id[int(share_id)] for share_id in share_ids]
    return tuple(definition_ids[:3]), tuple(definition_ids[3:])
