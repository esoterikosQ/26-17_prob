"""A-1d 디코더 검증 (예문·LM·계수는 손으로 만든 값)."""

from pathlib import Path

import pytest

from hnc.decoder import CandidateGenerator, Decoder, DecoderConfig
from hnc.jamo_channel import JamoChannel, estimate
from hnc.morph import MorphAnalyzer
from hnc.morph_lm import MorphLM

from hnc.jamo import ALPHABET

# 실제 계수처럼 관측되지 않은 편집의 평활 확률이 아주 작도록 다른 자모의 빈도를 크게 둔다.
EDITS = [{"a": ["ᆻ"], "b": ["ᆺ"], "c": 10}, {"a": ["<SP>"], "b": [], "c": 30},
         {"a": [], "b": ["<SP>"], "c": 50}]
ALPHAS = [{"a": [s], "c": 10 ** 6} for s in ALPHABET if s not in ("ᆻ", "<SP>")] + \
    [{"a": ["ᆻ"], "c": 100}, {"a": ["<SP>"], "c": 100}, {"a": [], "c": 10 ** 5}]
VOCAB = {"밥", "먹었어", "갔다", "집에"}
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def gen():
    params = estimate(EDITS, ALPHAS)
    return CandidateGenerator(params, VOCAB, JamoChannel(params), DecoderConfig())


def test_generator_spelling_split_merge(gen):
    assert "먹었어" in dict(gen.generate("먹엇어"))
    assert "밥 먹었어" in dict(gen.generate("밥먹었어"))
    assert "먹엇어" in dict(gen.generate("먹엇어"))                 # 동일 후보는 항상 포함
    assert dict(gen.generate("집 에", merged_only=True)).keys() == {"집에"}
    costs = dict(gen.generate("먹엇어"))
    assert costs["먹었어"] > costs["먹엇어"]                        # 교정은 채널 비용이 더 큼


@pytest.fixture(scope="module")
def dec(gen):
    return Decoder(gen, MorphLM(FIX / "tiny_lm.arpa"), MorphAnalyzer())


def test_decoder_corrects_spacing_and_spelling(dec):
    assert dec.decode("밥먹었어") == "밥 먹었어"
    assert dec.decode("집에 갓다.") == "집에 갔다."
    assert dec.decode("집 에 갔다") == "집에 갔다"


def test_decoder_keeps_when_lm_weight_zero(dec):
    cfg = DecoderConfig(lam_lm=0.0)
    assert dec.decode("집에 갓다", cfg) == "집에 갓다"           # 사전확률이 없으면 채널은 원문을 선호


def test_nbest_sorted_and_consistent(dec):
    x = "집에 갓다."
    nb = dec.search_nbest(x, dec.prepare(x), n_best=4)
    assert nb[0].text == dec.decode(x) == "집에 갔다."
    assert [h.score for h in nb] == sorted((h.score for h in nb), reverse=True)
    assert len({h.text for h in nb}) == len(nb) and all(h.n_morph > 0 for h in nb)


def test_decoder_adds_morph_channel_candidates(gen):
    from hnc.morph_channel import MorphChannel
    regions = [{"edits": [{"m": ["에", "JKB"], "m2": None, "z": "DEL", "lt": "NNG"}]}] * 5
    mc = MorphChannel.fit(regions, {("에", "JKB"): 50, ("집", "NNG"): 100}, {("NNG", "에", "JKB"): 20})
    d = Decoder(gen, MorphLM(FIX / "tiny_lm.arpa"), MorphAnalyzer(), morph_channel=mc)
    prep = d.prepare("집 갔다")
    cands = {y: c for y, c, _ in prep[(0, 1)]}
    assert "집에" in cands and "집" in cands                                  # 빠뜨린 조사 복원 후보(Kiwi join 실현)
    assert cands["집에"] > cands["집"]                                        # 채널은 원문을 선호
    plain = {y for y, _, _ in Decoder(gen, MorphLM(FIX / "tiny_lm.arpa"), MorphAnalyzer()).prepare("집 갔다")[(0, 1)]}
    assert "집에" not in plain


def test_explain_posterior_over_paths(gen):
    from hnc.morph_channel import MorphChannel
    regions = [{"edits": [{"m": ["에", "JKB"], "m2": None, "z": "DEL", "lt": "NNG"}]}] * 5
    mc = MorphChannel.fit(regions, {("에", "JKB"): 50, ("집", "NNG"): 100}, {("NNG", "에", "JKB"): 20})
    d = Decoder(gen, MorphLM(FIX / "tiny_lm.arpa"), MorphAnalyzer(), morph_channel=mc)
    x = "집 갓다"
    prep = d.prepare(x)
    nb = d.search_nbest(x, prep, n_best=8)
    hyp = next(h for h in nb if h.text == "집에 갔다")
    ex = d.explain(x, hyp)
    by_x = {e["x"]: e for e in ex}
    assert by_x["집"]["y"] == "집에" and by_x["집"]["posterior"] == {"PARTICLE_DEL": 1.0}
    assert by_x["갓다"]["posterior"] == {"JAMO": 1.0}
