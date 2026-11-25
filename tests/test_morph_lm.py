"""A-2 LM 증분 인터페이스와 선형 보간."""

import math
from pathlib import Path

import pytest

from hnc.morph_lm import InterpLM, MorphLM, load_lm

FIX = Path(__file__).parent / "fixtures"
A, B = FIX / "tiny_lm.arpa", FIX / "tiny_lm2.arpa"
TOKS = ["▁밥/NNG", "먹/VV", "었/EP", "어/EF", "▁모름/NNG"]


def _incremental(lm, toks):
    st = lm.begin()
    lp, st = lm.extend(st, toks[:2])
    lp2, st = lm.extend(st, toks[2:])
    return lp + lp2 + lm.end(st)


@pytest.mark.parametrize("lm_fn", [lambda: MorphLM(A), lambda: InterpLM([A, B], [0.3, 0.7])])
def test_incremental_equals_sentence_score(lm_fn):
    lm = lm_fn()
    assert _incremental(lm, TOKS) == pytest.approx(lm.score_tokens(TOKS), abs=1e-4)


def test_interp_of_identical_models_is_identity():
    assert InterpLM([A, A], [0.4, 0.6]).score_tokens(TOKS) == pytest.approx(MorphLM(A).score_tokens(TOKS), abs=1e-4)


def test_interp_is_tokenwise_mixture():
    a, b = MorphLM(A), MorphLM(B)
    w = 0.25
    want = sum(math.log(w * 10 ** x + (1 - w) * 10 ** y) for x, y in zip(a.token_log10(TOKS), b.token_log10(TOKS)))
    assert InterpLM([A, B], [w, 1 - w]).score_tokens(TOKS) == pytest.approx(want, abs=1e-4)
    assert a.score_tokens(TOKS) != pytest.approx(b.score_tokens(TOKS), abs=1e-2)


def test_load_lm_dispatch_and_zero_weight():
    assert isinstance(load_lm(str(A)), MorphLM)
    lm = load_lm(f"{A},{B}", "0,1")
    assert isinstance(lm, InterpLM) and len(lm.parts) == 1
    assert lm.score_tokens(TOKS) == pytest.approx(MorphLM(B).score_tokens(TOKS), abs=1e-4)
