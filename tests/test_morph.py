"""A-0b 형태소 분석·표층 실현 단위 검증 (예문은 직접 작성한 문장)."""

import pytest

from hnc.morph import Morph, MorphAnalyzer, diff_segments, normalize_space, roundtrip_category


@pytest.fixture(scope="module")
def ana():
    return MorphAnalyzer()


def test_space_flags_follow_eojeol_boundaries(ana):
    ms = ana.analyze("학교에 갔었다.")
    assert ms[0] == Morph("학교", "NNG", False)
    assert [m.space_before for m in ms].count(True) == 1
    first_after_space = next(m for m in ms if m.space_before)
    assert first_after_space.form == "가"


@pytest.mark.parametrize("text", [
    "학교에 갔었다.",
    "길을 물어요.",
    "날씨가 더워서 집에 있었다.",
    "그는 책을 읽고 있다.",
])
def test_realize_round_trip(ana, text):
    assert ana.realize(ana.analyze(text)) == text


def test_realize_handles_allomorphy_and_irregulars(ana):
    # 조사 이형태: 학교 + 을 → 학교를
    assert ana.realize([Morph("학교", "NNG", False), Morph("을", "JKO", False)]) == "학교를"
    # 축약: 하 + 었 + 다 → 했다
    assert ana.realize([Morph("하", "VV", False), Morph("었", "EP", False),
                        Morph("다", "EF", False)]) == "했다"


def test_roundtrip_category():
    assert roundtrip_category("가 나", "가 나") == "exact"
    assert roundtrip_category("가  나 ", "가 나") == "space"
    assert roundtrip_category("가 나", "가나다") == "other"
    assert normalize_space("  가   나 ") == "가 나"
    assert diff_segments("학교을", "학교를") == [("을", "를")]
