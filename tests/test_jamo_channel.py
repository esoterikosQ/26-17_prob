"""A-1b 자모 채널 추정·WFST 검증 (계수는 손으로 만든 값)."""

import math

import pytest

from hnc.jamo_channel import ChannelParams, JamoChannel, estimate

EDITS = [{"a": ["ᆻ"], "b": ["ᆺ"], "c": 10}, {"a": ["<SP>"], "b": [], "c": 30},
         {"a": ["ᅡ", "ᆻ"], "b": ["ᅡ", "ᆺ"], "c": 5}, {"a": ["ᅡ", "ᆻ", "ᄃ"], "b": ["ᅡ", "ᆺ", "ᄃ"], "c": 1}]
ALPHAS = [{"a": ["ᆻ"], "c": 100}, {"a": ["<SP>"], "c": 100}, {"a": ["ᅡ", "ᆻ"], "c": 50},
          {"a": ["ᅡ", "ᆻ", "ᄃ"], "c": 20}, {"a": [], "c": 1000}]


def test_estimate():
    p = estimate(EDITS, ALPHAS, kappa=1.0, min_count=2)
    assert p.edits[(("ᆻ",), ("ᆺ",))] == pytest.approx((10 + 1 / 51) / 101)
    assert p.edits[(("ᅡ", "ᆻ"), ("ᅡ", "ᆺ"))] == pytest.approx(0.1)
    assert (("ᅡ", "ᆻ", "ᄃ"), ("ᅡ", "ᆺ", "ᄃ")) not in p.edits          # min_count 미만
    out = sum(v for (a, _), v in p.edits.items() if a == ("ᆻ",))
    assert p.identity["ᆻ"] == pytest.approx(1 - out)
    assert all(v > 0 for v in p.edits.values())
    assert ChannelParams.from_json(p.to_json()).edits == p.edits


@pytest.fixture(scope="module")
def ch():
    return JamoChannel(estimate(EDITS, ALPHAS), other_symbols=["!", "?", "A", "B"])


def test_channel_prefers_identity_and_scores_typos(ch):
    same = ch.neglogprob("갔다", "갔다")
    typo = ch.neglogprob("갔다", "갓다")
    space = ch.neglogprob("밥 먹어", "밥먹어")
    assert 0 < same < typo < math.inf
    assert math.isfinite(space)


def test_full_support(ch):
    assert math.isfinite(ch.neglogprob("가!", "나?"))
    assert math.isfinite(ch.neglogprob("가", "AB"))
    assert math.isfinite(ch.neglogprob("가", "가€"))     # 표에 없는 기호 → <UNKSYM>
