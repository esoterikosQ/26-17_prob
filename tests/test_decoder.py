"""A-1d 디코더 검증 (예문·LM·계수는 손으로 만든 값)."""

from pathlib import Path

import pytest

from hnc.decoder import CandidateGenerator, Decoder, DecoderConfig
from hnc.jamo_channel import JamoChannel, estimate
from hnc.morph import MorphAnalyzer
from hnc.morph_lm import MorphLM

EDITS = [{"a": ["ᆻ"], "b": ["ᆺ"], "c": 10}, {"a": ["<SP>"], "b": [], "c": 30}]
ALPHAS = [{"a": ["ᆻ"], "c": 100}, {"a": ["<SP>"], "c": 100}, {"a": [], "c": 1000}]
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
