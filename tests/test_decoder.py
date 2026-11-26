"""A-1d 디코더 검증 (예문·LM·계수는 손으로 만든 값)."""

import math
from dataclasses import replace
from pathlib import Path

import pytest

from hnc.decoder import CandidateGenerator, Decoder, DecoderConfig, PreparedSentence
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

    # 양의 질량을 주지 않은 형태소 경로는 설명에서만 지우는 것이 아니라 빔에 들어가기 전부터 제외한다.
    no_m = d.search_nbest(x, prep, replace(d.cfg, morph_weight=0), 8)
    jamo = Decoder(gen, d.lm, d.ana)
    plain = jamo.search_nbest(x, jamo.prepare(x), n_best=8)
    assert [(h.text, h.score) for h in no_m] == [(h.text, h.score) for h in plain]


@pytest.mark.parametrize("weight", [0, .1, 1, 3])
def test_same_surface_mass_and_explanation_use_same_weight(gen, weight):
    d = Decoder(gen, MorphLM(FIX / "tiny_lm.arpa"), MorphAnalyzer(), morph_channel=object())
    p = PreparedSentence()
    p[(0, 1)] = [("밥", 0., ["▁밥/NNG"]), ("집", -math.log(.375), ["▁집/NNG"])]
    p.paths = {(0, "밥"): {"IDENT": 0.}, (0, "집"): {"JAMO": -math.log(.125), "PARTICLE": -math.log(.25)}}
    h = next(h for h in d.search_nbest("밥", p, replace(d.cfg, morph_weight=weight)) if h.text == "집")
    total = .125 + .25 * weight
    assert h.ch == pytest.approx(-math.log(total))
    expected = {"JAMO": .125 / total}
    if weight:
        expected["PARTICLE"] = .25 * weight / total
    assert d.explain("밥", h)[0]["posterior"] == pytest.approx(expected)


@pytest.mark.parametrize("weight", [-1, math.inf, math.nan])
def test_morph_weight_validation(weight):
    with pytest.raises(ValueError):
        DecoderConfig(morph_weight=weight)


def test_optional_lexical_path_does_not_replace_grammar_budget(gen):
    from hnc.morph_channel import MorphChannel
    mc = MorphChannel({"떡/NNG": [[["밥", "NNG"], "LEXICAL", 2., 8]]}, {}, {}, {})
    lm, ana = MorphLM(FIX / "tiny_lm.arpa"), MorphAnalyzer()
    off = Decoder(gen, lm, ana, morph_channel=mc)
    on = Decoder(gen, lm, ana, DecoderConfig(lexical_k=1), morph_channel=mc)
    old, prep = off.prepare("떡"), on.prepare("떡")
    assert "밥" not in {y for y, _, _ in old[(0, 1)]}
    assert {y for y, _, _ in old[(0, 1)]} < {y for y, _, _ in prep[(0, 1)]}
    h = next(h for h in on.search_nbest("떡", prep) if h.text == "밥")
    assert on.explain("떡", h)[0]["posterior"] == {"LEXICAL_NOUN": 1.0}
    assert "밥" not in {h.text for h in on.search_nbest("떡", prep, replace(on.cfg, morph_weight=0))}


def test_core_prior_uses_pcfg_and_retains_cache_order_on_ties():
    from scripts.core_ablation_controls import prior_rank
    hyps = [{"text": "나", "lm": -3., "pcfg": -10.}, {"text": "가", "lm": -4., "pcfg": -1.}]
    assert prior_rank(hyps) == 1
    hyps[0].update(lm=-4., pcfg=-1., ch=1000.)
    assert prior_rank(hyps) == 0


def test_core_morph_only_rejects_jamo_changes_but_keeps_particle_restoration(gen):
    from hnc.morph_channel import MorphChannel
    from scripts.core_ablation_controls import audit_morph_candidates, make_decoder
    regions = [{"edits": [{"m": ["에", "JKB"], "m2": None, "z": "DEL", "lt": "NNG"}]}] * 5
    mc = MorphChannel.fit(regions, {("에", "JKB"): 50, ("집", "NNG"): 100}, {("NNG", "에", "JKB"): 20})
    d = make_decoder(gen.vocab, MorphLM(FIX / "tiny_lm.arpa"), MorphAnalyzer(), mc)
    x = "집 에 갓다 밥먹었어"
    prep = d.prepare(x)
    assert audit_morph_candidates(x, prep)["morph_change"] > 0
    assert set(prep.paths[0, "집에"]) == {"PARTICLE_DEL"}
    assert (0, 2) not in prep and "밥 먹었어" not in {y for y, _, _ in prep[3, 1]}
    assert "갔다" not in {y for y, _, _ in prep[2, 1]}
    prep.paths[0, "집에"]["JAMO"] = 2.
    with pytest.raises(ValueError, match="morphological support"):
        audit_morph_candidates(x, prep)
