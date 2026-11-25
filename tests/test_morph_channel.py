"""A-3a 형태소 편집 추출: 형태소 정렬, 자동 z, 통용 변이 판정 (예시는 직접 만든 것)."""

from hnc.morph_channel import (
    KAGAS_TO_Z, align_morphs, classify_edit, is_punct, morph_edits, region_z, strip_punct, variant_by_freq,
)

FREQ = {"정말": 92549, "완전": 19851, "있어서": 43734, "잇어서": 2, "라테": 499, "라떼": 1158}


def freq(w):
    return FREQ.get(w, 0)


def zs(my, mx, variant=lambda a, b: False):
    return [e["z"] for e in morph_edits(my, mx, variant)]


def test_particle_ending_insertion_deletion():
    assert zs([("학교", "NNG"), ("를", "JKO")], [("학교", "NNG"), ("을", "JKO")]) == ["IDENT", "PARTICLE"]
    assert zs([("가", "VV"), ("았", "EP"), ("다", "EF")], [("가", "VV"), ("았", "EP"), ("고", "EC")])[-1] == "ENDING"
    # 쓴 사람이 조사를 더 씀(x에만 있음) = INS, 빠뜨림 = DEL
    assert zs([("정년", "NNG")], [("정년", "NNG"), ("을", "JKO")]) == ["IDENT", "INS"]
    assert zs([("나", "NP"), ("는", "JX")], [("나", "NP")]) == ["IDENT", "DEL"]


def test_alignment_prefers_same_class_substitution():
    pairs = align_morphs([("책", "NNG"), ("을", "JKO"), ("읽", "VV")], [("책", "NNG"), ("이", "JKS"), ("읽", "VV")])
    assert pairs[1] == (("을", "JKO"), ("이", "JKS"))


def test_spelling_vs_variant_vs_lexical():
    typo = variant_by_freq(freq, "있어서", "잇어서")
    assert classify_edit(("있", "VA"), ("잇", "NNG"), typo) == "SPELL"
    colloquial = variant_by_freq(freq, "정말", "완전")
    assert classify_edit(("정말", "MAG"), ("완전", "MAG"), colloquial) == "VARIANT"
    loan = variant_by_freq(freq, "라테", "라떼")
    assert classify_edit(("라테", "NNP"), ("라떼", "NNP"), loan) == "VARIANT"   # 자모 1개 차이라도 통용 표기
    assert classify_edit(("사과", "NNG"), ("배추", "NNG")) == "LEXICAL"


def test_variant_falls_back_to_free_morpheme_forms():
    # 어절 빈도가 둘 다 0이면 홀로 쓰이는 형태끼리 비교
    v = variant_by_freq(lambda w: {"정말": 100, "완전": 50}.get(w, 0), "정말이지요", "완전이지요")
    assert v(("정말", "MAG"), ("완전", "MAG"))
    assert not v(("있", "VA"), ("잇", "VV"))      # 용언 어간은 형태 빈도로 판정하지 않음


def test_punct_stripping_keeps_jamo_sw():
    assert is_punct((",", "SP")) and is_punct(("!", "SF"))
    assert not is_punct(("ㅇㅇ", "SW"))
    assert strip_punct([("그래", "IC"), (",", "SP")]) == [("그래", "IC")]
    assert zs([("그래", "IC"), (",", "SP")], [("그래", "IC")]) == ["IDENT"]


def test_region_z_priority_and_surface_only():
    assert region_z([{"z": "IDENT"}, {"z": "IDENT"}]) == "SPELL"
    assert region_z([{"z": "SPELL"}, {"z": "PARTICLE"}]) == "PARTICLE"
    assert KAGAS_TO_Z["INSERTION"] == "DEL" and KAGAS_TO_Z["DELETION"] == "INS"
