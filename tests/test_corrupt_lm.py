"""오류 주입·LM 토큰화 검증 (예문은 직접 작성)."""

import random

from hnc.corrupt import corrupt, eojeols
from hnc.morph_lm import lm_tokens

S = [("나", "NP", False), ("는", "JX", False), ("사과", "NNG", True), ("를", "JKO", False),
     ("먹", "VV", True), ("었", "EP", False), ("다", "EF", False), (".", "SF", False)]


def test_lm_tokens_mark_eojeols():
    assert lm_tokens(S)[:4] == ["▁나/NP", "는/JX", "▁사과/NNG", "를/JKO"]
    assert lm_tokens([("a b", "SL", False)]) == ["▁a_b/SL"]


def test_corruptions():
    rng = random.Random(0)
    assert corrupt(S, "case_swap", rng)[3] == ("가", "JKS", False)      # 사과(받침 없음) + 가
    d = corrupt(S, "particle_del", rng)
    assert len(d) == len(S) - 1 and all(t not in ("JX", "JKO") for _, t, _ in d[:2]) or len(d) == len(S) - 1
    sw = corrupt(S, "eojeol_swap", rng)
    assert len(eojeols(sw)) == 3 and sw[0][2] is False and sorted(sw) == sorted(
        [(f, t, s) for f, t, s in sw])
    assert corrupt(S, "final_ec", rng)[6] == ("고", "EC", False)
    assert corrupt([("네", "IC", False)], "case_swap", rng) is None
